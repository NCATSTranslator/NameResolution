"""Solr HTTP client and response parsers for the NameRes status endpoint."""
import asyncio
import logging

import httpx

logger = logging.getLogger(__name__)

# Primary core name we monitor.
_CORE_NAME = "name_lookup_shard1_replica_n1"


class SolrClient:
    """HTTP client for Solr admin APIs with static parsing utilities.

    Async fetch methods make HTTP calls and return raw JSON (or None on
    failure).  Static parse_* methods accept raw JSON dicts and can be
    unit-tested directly without a running Solr instance.
    """

    def __init__(self, host: str, port: int):
        self._base = f"http://{host}:{port}/solr"

    # ------------------------------------------------------------------ #
    # Static parsers — pure functions, independently unit-testable        #
    # ------------------------------------------------------------------ #

    @staticmethod
    def parse_jvm(sysinfo_data: dict) -> dict:
        """Parse JVM heap stats from a /admin/info/system response."""
        jvm_raw = sysinfo_data.get("jvm", {}).get("memory", {}).get("raw", {})
        heap_used = jvm_raw.get("used")
        heap_max = jvm_raw.get("max")
        return {
            "heap_used_bytes": heap_used,
            "heap_max_bytes": heap_max,
            "heap_used_pct": round(heap_used / heap_max, 4) if heap_used and heap_max else None,
        }

    @staticmethod
    def parse_os(sysinfo_data: dict) -> dict:
        """Parse OS / memory stats from a /admin/info/system response."""
        system = sysinfo_data.get("system", {})
        free_mem = system.get("freePhysicalMemorySize")
        total_mem = system.get("totalPhysicalMemorySize")
        if free_mem is not None and total_mem and total_mem > 0 and 0 <= free_mem <= total_mem:
            physical_memory_used_pct = round((total_mem - free_mem) / total_mem, 4)
        else:
            physical_memory_used_pct = None
        return {
            "process_cpu_load": system.get("processCpuLoad"),
            "system_cpu_load": system.get("systemCpuLoad"),
            "free_physical_memory_bytes": free_mem,
            "total_physical_memory_bytes": total_mem,
            "physical_memory_used_pct": physical_memory_used_pct,
        }

    @staticmethod
    def parse_cache(mbeans_data: dict, name: str) -> dict | None:
        """Parse stats for one named cache from a /admin/mbeans?cat=CACHE response.

        Solr stores stats under fully-qualified keys such as
        ``CACHE.searcher.<name>.hitratio``, not bare ``hitratio``.
        Returns None when the cache is absent from the response.
        """
        for entry in mbeans_data.get("solr-mbeans", []):
            if isinstance(entry, dict) and name in entry:
                stats = entry[name].get("stats", {})
                p = f"CACHE.searcher.{name}."
                return {
                    "hitratio": stats.get(f"{p}hitratio"),
                    "evictions": stats.get(f"{p}evictions"),
                    "size":      stats.get(f"{p}size"),
                    "lookups":   stats.get(f"{p}lookups"),
                    "hits":      stats.get(f"{p}hits"),
                }
        return None

    @staticmethod
    def parse_index(cores_data: dict) -> dict | None:
        """Parse core index info from a /admin/cores?action=STATUS response.

        Returns None when the expected core is not present.
        """
        core = cores_data.get("status", {}).get(_CORE_NAME)
        if core is None:
            return None
        index = core.get("index", {})
        return {
            "startTime":    core.get("startTime"),
            "numDocs":      index.get("numDocs", ""),
            "maxDoc":       index.get("maxDoc", ""),
            "deletedDocs":  index.get("deletedDocs", ""),
            "version":      index.get("version", ""),
            "segmentCount": index.get("segmentCount", ""),
            "lastModified": index.get("lastModified", ""),
            "size":         index.get("size", ""),
        }

    # ------------------------------------------------------------------ #
    # Async HTTP fetchers                                                  #
    # ------------------------------------------------------------------ #

    async def fetch_sysinfo(self, client: httpx.AsyncClient) -> dict | None:
        try:
            r = await client.get(f"{self._base}/admin/info/system", params={"wt": "json"})
            r.raise_for_status()
            return r.json()
        except Exception as e:
            logger.warning("Could not fetch Solr system info: %s", e)
            return None

    async def fetch_mbeans(self, client: httpx.AsyncClient) -> dict | None:
        try:
            r = await client.get(
                f"{self._base}/name_lookup/admin/mbeans",
                params={"cat": "CACHE", "stats": "true", "wt": "json"},
            )
            r.raise_for_status()
            return r.json()
        except Exception as e:
            logger.warning("Could not fetch Solr cache MBeans: %s", e)
            return None

    async def fetch_cores(self, client: httpx.AsyncClient) -> dict:
        r = await client.get(f"{self._base}/admin/cores", params={"action": "STATUS"})
        if r.status_code >= 300:
            logger.error("Solr error on /solr/admin/cores?action=STATUS: %s", r.text)
            r.raise_for_status()
        return r.json()

    # ------------------------------------------------------------------ #
    # High-level: fetch everything and return a parsed snapshot           #
    # ------------------------------------------------------------------ #

    async def fetch_status(self) -> dict:
        """Fetch and parse all Solr monitoring data concurrently.

        Returns a dict with a ``found`` flag plus parsed fields.  Callers
        should check ``result["found"]`` before accessing index-level keys.

        Raises ``httpx.HTTPStatusError`` if the cores endpoint is unavailable.
        """
        async with httpx.AsyncClient(timeout=None) as client:
            cores_data, sysinfo_data, mbeans_data = await asyncio.gather(
                self.fetch_cores(client),
                self.fetch_sysinfo(client),
                self.fetch_mbeans(client),
            )

        jvm_info   = self.parse_jvm(sysinfo_data)   if sysinfo_data else None
        os_info    = self.parse_os(sysinfo_data)    if sysinfo_data else None
        cache_info = {
            "filterCache":      self.parse_cache(mbeans_data, "filterCache"),
            "queryResultCache": self.parse_cache(mbeans_data, "queryResultCache"),
        } if mbeans_data else None

        index_info = self.parse_index(cores_data)
        if index_info is None:
            return {"found": False, "jvm": jvm_info, "os": os_info}

        return {
            "found":        True,
            "startTime":    index_info["startTime"],
            "numDocs":      index_info["numDocs"],
            "maxDoc":       index_info["maxDoc"],
            "deletedDocs":  index_info["deletedDocs"],
            "version":      index_info["version"],
            "segmentCount": index_info["segmentCount"],
            "lastModified": index_info["lastModified"],
            "size":         index_info["size"],
            "jvm":          jvm_info,
            "os":           os_info,
            "cache":        cache_info,
        }
