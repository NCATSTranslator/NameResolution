"""Unit tests for SolrClient static parsing methods.

These tests exercise the pure parsing logic without any HTTP calls or a
running Solr instance.  The mock payloads mirror the actual structure
returned by Solr's admin endpoints.
"""
from api.solr import SolrClient


# ------------------------------------------------------------------ #
# parse_cache                                                          #
# ------------------------------------------------------------------ #

# Minimal mbeans payload matching real Solr structure:
# solr-mbeans is an alternating [category-string, {entries...}, ...] list.
_MBEANS = {
    "solr-mbeans": [
        "CACHE",
        {
            "filterCache": {
                "class": "org.apache.solr.search.CaffeineCache",
                "description": "Caffeine Cache(maxSize=512, initialSize=512)",
                "stats": {
                    "CACHE.searcher.filterCache.hitratio": 0.9,
                    "CACHE.searcher.filterCache.evictions": 10,
                    "CACHE.searcher.filterCache.size": 500,
                    "CACHE.searcher.filterCache.lookups": 1000,
                    "CACHE.searcher.filterCache.hits": 900,
                },
            },
            "queryResultCache": {
                "class": "org.apache.solr.search.CaffeineCache",
                "description": "Caffeine Cache(maxSize=512, initialSize=512)",
                "stats": {
                    "CACHE.searcher.queryResultCache.hitratio": 0.5756,
                    "CACHE.searcher.queryResultCache.evictions": 2310,
                    "CACHE.searcher.queryResultCache.size": 512,
                    "CACHE.searcher.queryResultCache.lookups": 6649,
                    "CACHE.searcher.queryResultCache.hits": 3827,
                },
            },
        },
    ]
}


def test_parse_cache_queryResultCache():
    result = SolrClient.parse_cache(_MBEANS, "queryResultCache")
    assert result == {
        "hitratio": 0.5756,
        "evictions": 2310,
        "size": 512,
        "lookups": 6649,
        "hits": 3827,
    }


def test_parse_cache_filterCache():
    result = SolrClient.parse_cache(_MBEANS, "filterCache")
    assert result == {
        "hitratio": 0.9,
        "evictions": 10,
        "size": 500,
        "lookups": 1000,
        "hits": 900,
    }


def test_parse_cache_missing_name():
    assert SolrClient.parse_cache(_MBEANS, "perSegFilter") is None


def test_parse_cache_empty_mbeans():
    assert SolrClient.parse_cache({}, "queryResultCache") is None


def test_parse_cache_empty_stats():
    """Cache present but no stats entries — all values None, not an error."""
    data = {"solr-mbeans": ["CACHE", {"queryResultCache": {"stats": {}}}]}
    result = SolrClient.parse_cache(data, "queryResultCache")
    assert result == {
        "hitratio": None,
        "evictions": None,
        "size": None,
        "lookups": None,
        "hits": None,
    }


# ------------------------------------------------------------------ #
# parse_jvm                                                            #
# ------------------------------------------------------------------ #

def test_parse_jvm():
    data = {"jvm": {"memory": {"raw": {"used": 500_000_000, "max": 1_000_000_000}}}}
    result = SolrClient.parse_jvm(data)
    assert result["heap_used_bytes"] == 500_000_000
    assert result["heap_max_bytes"] == 1_000_000_000
    assert result["heap_used_pct"] == 0.5


def test_parse_jvm_missing_data():
    result = SolrClient.parse_jvm({})
    assert result["heap_used_bytes"] is None
    assert result["heap_max_bytes"] is None
    assert result["heap_used_pct"] is None


# ------------------------------------------------------------------ #
# parse_os                                                             #
# ------------------------------------------------------------------ #

def test_parse_os():
    data = {
        "system": {
            "processCpuLoad": 0.25,
            "systemCpuLoad": 0.40,
            "freePhysicalMemorySize": 2_000_000_000,
            "totalPhysicalMemorySize": 8_000_000_000,
        }
    }
    result = SolrClient.parse_os(data)
    assert result["process_cpu_load"] == 0.25
    assert result["system_cpu_load"] == 0.40
    assert result["free_physical_memory_bytes"] == 2_000_000_000
    assert result["total_physical_memory_bytes"] == 8_000_000_000
    assert result["physical_memory_used_pct"] == 0.75


def test_parse_os_invalid_memory_free_greater_than_total():
    data = {
        "system": {
            "freePhysicalMemorySize": 9_000_000_000,
            "totalPhysicalMemorySize": 8_000_000_000,
        }
    }
    result = SolrClient.parse_os(data)
    assert result["physical_memory_used_pct"] is None


def test_parse_os_zero_total():
    data = {"system": {"freePhysicalMemorySize": 0, "totalPhysicalMemorySize": 0}}
    result = SolrClient.parse_os(data)
    assert result["physical_memory_used_pct"] is None


def test_parse_os_missing_data():
    result = SolrClient.parse_os({})
    assert result["process_cpu_load"] is None
    assert result["physical_memory_used_pct"] is None


# ------------------------------------------------------------------ #
# parse_index                                                          #
# ------------------------------------------------------------------ #

_CORES = {
    "status": {
        "name_lookup_shard1_replica_n1": {
            "startTime": "2025-12-19T21:20:02.292Z",
            "index": {
                "numDocs": 89,
                "maxDoc": 89,
                "deletedDocs": 0,
                "version": 42,
                "segmentCount": 3,
                "lastModified": "2025-12-19T21:21:00.000Z",
                "size": "10 MB",
            },
        }
    }
}


def test_parse_index():
    result = SolrClient.parse_index(_CORES)
    assert result["startTime"] == "2025-12-19T21:20:02.292Z"
    assert result["numDocs"] == 89
    assert result["maxDoc"] == 89
    assert result["deletedDocs"] == 0
    assert result["size"] == "10 MB"


def test_parse_index_core_not_found():
    assert SolrClient.parse_index({"status": {}}) is None


def test_parse_index_empty():
    assert SolrClient.parse_index({}) is None
