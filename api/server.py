"""Biomedical entity name resolution service.

1) split the input into fragments at spaces
  * The order does not matter
2) search for names including all fragments, case insensitive
3) sort by length, ascending
  * The curie with the shortest match is first, etc.
  * Matching names are returned first, followed by non-matching names
"""
import json
import logging, warnings
import os
import re
from typing import Dict, List, Union, Annotated, Optional

from fastapi import Body, FastAPI, Query
from fastapi.responses import RedirectResponse
import httpx
from pydantic import BaseModel, conint, Field
from starlette.middleware.cors import CORSMiddleware

from .apidocs import get_app_info, construct_open_api_schema

LOGGER = logging.getLogger(__name__)
SOLR_HOST = os.getenv("SOLR_HOST", "localhost")
SOLR_PORT = os.getenv("SOLR_PORT", "8983")

app = FastAPI(**get_app_info())

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ENDPOINT /
# If someone tries accessing /, we should redirect them to the Swagger interface.
@app.get("/", include_in_schema=False)
async def docs_redirect():
    """
    Redirect requests to `/` (where we don't have any content) to `/docs` (which is our Swagger interface).
    """
    return RedirectResponse(url='/docs')


@app.get("/status",
         summary="Get status and counts for this NameRes instance.",
         description="This endpoint will return status information and a list of counts from the underlying Solr "
                     "instance for this NameRes instance."
         )
async def status_get() -> Dict:
    """ Return status and count information from the underyling Solr instance. """
    return await status()


async def status() -> Dict:
    """ Return a dictionary containing status and count information for the underlying Solr instance. """
    query_url = f"http://{SOLR_HOST}:{SOLR_PORT}/solr/admin/cores"
    async with httpx.AsyncClient(timeout=None) as client:
        response = await client.get(query_url, params={
            'action': 'STATUS'
        })
    if response.status_code >= 300:
        LOGGER.error("Solr error on accessing /solr/admin/cores?action=STATUS: %s", response.text)
        response.raise_for_status()
    result = response.json()

    # Do we know the Babel version and version URL? It will be stored in an environmental variable if we do.
    babel_version = os.environ.get("BABEL_VERSION", "unknown")
    babel_version_url = os.environ.get("BABEL_VERSION_URL", "")

    # We should have a status for name_lookup_shard1_replica_n1.
    if 'status' in result and 'name_lookup_shard1_replica_n1' in result['status']:
        core = result['status']['name_lookup_shard1_replica_n1']

        index = {}
        if 'index' in core:
            index = core['index']

        return {
            'status': 'ok',
            'message': 'Reporting results from primary core.',
            'babel_version': babel_version,
            'babel_version_url': babel_version_url,
            'startTime': core['startTime'],
            'numDocs': index.get('numDocs', ''),
            'maxDoc': index.get('maxDoc', ''),
            'deletedDocs': index.get('deletedDocs', ''),
            'version': index.get('version', ''),
            'segmentCount': index.get('segmentCount', ''),
            'lastModified': index.get('lastModified', ''),
            'size': index.get('size', ''),
        }
    else:
        return {
            'status': 'error',
            'message': 'Expected core not found.'
        }


# ENDPOINT /reverse_lookup

class Request(BaseModel):
    """Reverse-lookup request body."""
    curies: List[str]

class SynonymsRequest(BaseModel):
    """ Synonyms search request body. """
    preferred_curies: List[str]

@app.get(
    "/reverse_lookup",
    summary="Look up synonyms for a CURIE.",
    description="Returns a list of synonyms for a particular CURIE.",
    response_model=Dict[str, Dict],
    tags=["lookup"],
    deprecated=True,
)
async def reverse_lookup_get(
        curies: List[str]= Query(
            example=["MONDO:0005737", "MONDO:0009757"],
            description="A list of CURIEs to look up synonyms for."
        )
) -> Dict[str, Dict]:
    """Returns a list of synonyms for a particular CURIE."""
    return await reverse_lookup(curies)


@app.get(
    "/synonyms",
    summary="Look up synonyms for a CURIE.",
    description="Returns a list of synonyms for a particular preferred CURIE. You can normalize a CURIE to a preferred CURIE using NodeNorm.",
    response_model=Dict[str, Dict],
    tags=["lookup"],
)
async def lookup_names_get(
        preferred_curies: List[str]= Query(
            example=["MONDO:0005737", "MONDO:0009757"],
            description="A list of CURIEs to look up synonyms for."
        )
) -> Dict[str, Dict]:
    """Returns a list of synonyms for a particular CURIE."""
    return await reverse_lookup(preferred_curies)


@app.post(
    "/reverse_lookup",
    summary="Look up synonyms for a CURIE.",
    description="Returns a list of synonyms for a particular CURIE.",
    response_model=Dict[str, Dict],
    tags=["lookup"],
    deprecated=True,
)
async def lookup_names_post(
        request: Request = Body(..., example={
            "curies": ["MONDO:0005737", "MONDO:0009757"],
        }),
) -> Dict[str, Dict]:
    """Returns a list of synonyms for a particular CURIE."""
    return await reverse_lookup(request.curies)


@app.post(
    "/synonyms",
    summary="Look up synonyms for a CURIE.",
    description="Returns a list of synonyms for a particular preferred CURIE. You can normalize a CURIE to a preferred CURIE using NodeNorm.",
    response_model=Dict[str, Dict],
    tags=["lookup"],
)
async def lookup_names_post(
        request: SynonymsRequest = Body(..., example={
            "preferred_curies": ["MONDO:0005737", "MONDO:0009757"],
        }),
) -> Dict[str, Dict]:
    """Returns a list of synonyms for a particular CURIE."""
    return await reverse_lookup(request.preferred_curies)


async def reverse_lookup(curies) -> Dict[str, Dict]:
    """Returns a list of synonyms for a particular CURIE."""
    query = f"http://{SOLR_HOST}:{SOLR_PORT}/solr/name_lookup/select"
    curie_filter = " OR ".join(
        f"curie:\"{curie}\""
        for curie in curies
    )
    params = {
        "query": curie_filter,
        "limit": 1000000,
    }
    async with httpx.AsyncClient(timeout=None) as client:
        response = await client.post(query, json=params)
    response.raise_for_status()
    response_json = response.json()
    output = {
        curie: {}
        for curie in curies
    }
    for doc in response_json["response"]["docs"]:
        output[doc["curie"]] = doc
    return output

class LookupResult(BaseModel):
    curie:str
    label: str
    highlighting: Dict[str, List[str]]
    synonyms: List[str]
    taxa: List[str]
    types: List[str]
    score: float
    clique_identifier_count: int


@app.get("/lookup",
     summary="Look up cliques for a fragment of a name or synonym.",
     description="Returns cliques with a name or synonym that contains a specified string.",
     response_model=List[LookupResult],
     tags=["lookup"]
)
async def lookup_curies_get(
        string: Annotated[str, Query(
            description="The string to search for."
        )],
        autocomplete: Annotated[bool, Query(
            description="Is the input string incomplete (autocomplete=true) or a complete phrase (autocomplete=false)?"
        )] = True,
        highlighting: Annotated[bool, Query(
            description="Return information on which labels and synonyms matched the search query?"
        )] = False,
        offset: Annotated[int, Query(
            description="The number of results to skip. Can be used to page through the results of a query.",
            # Offset should be greater than or equal to zero.
            ge=0
        )] = 0,
        limit: Annotated[int, Query(
            description="The number of results to skip. Can be used to page through the results of a query.",
            # Limit should be greater than or equal to zero and less than or equal to 1000.
            ge=0,
            le=1000
        )] = 10,
        biolink_type: Annotated[Union[List[str], None], Query(
            description="The Biolink types to filter to (with or without the `biolink:` prefix), "
                        "e.g. `biolink:Disease` or `Disease`. Multiple types will be combined with OR, i.e. filtering "
                        "for PhenotypicFeature and Disease will return concepts that are either PhenotypicFeatures OR "
                        "Disease, not concepts that are both PhenotypicFeature AND Disease.",
            # We can't use `example` here because otherwise it gets filled in when you click "Try it out",
            # which is easy to overlook.
            # example=["biolink:Disease", "biolink:PhenotypicFeature"]
        )] = [],
        only_prefixes: Annotated[Union[str, None], Query(
            description="Pipe-separated, case-sensitive list of prefixes to filter to, e.g. `MONDO|EFO`.",
            # We can't use `example` here because otherwise it gets filled in when filling this in.
            # example="MONDO|EFO"
        )] = None,
        exclude_prefixes: Annotated[Union[str, None], Query(
            description="Pipe-separated, case-sensitive list of prefixes to exclude, e.g. `UMLS|EFO`.",
            # We can't use `example` here because otherwise it gets filled in when filling this in.
            # example="UMLS|EFO"
        )] = None,
        only_taxa: Annotated[Union[str, None], Query(
            description="Pipe-separated, case-sensitive list of taxa to filter, "
                        "e.g. `NCBITaxon:9606|NCBITaxon:10090|NCBITaxon:10116|NCBITaxon:7955`.",
            # We can't use `example` here because otherwise it gets filled in when filling this in.
            # example="NCBITaxon:9606|NCBITaxon:10090|NCBITaxon:10116|NCBITaxon:7955"
        )] = None
) -> List[LookupResult]:
    """
    Returns cliques with a name or synonym that contains a specified string.
    """
    return await lookup(string, autocomplete, highlighting, offset, limit, biolink_type, only_prefixes, exclude_prefixes, only_taxa)


@app.post("/lookup",
    summary="Look up cliques for a fragment of a name or synonym.",
    description="Returns cliques with a name or synonym that contains a specified string.",
    response_model=List[LookupResult],
    tags=["lookup"]
)
async def lookup_curies_post(
        string: Annotated[str, Query(
            description="The string to search for."
        )],
        autocomplete: Annotated[bool, Query(
            description="Is the input string incomplete (autocomplete=true) or a complete phrase (autocomplete=false)?"
        )] = True,
        highlighting: Annotated[bool, Query(
            description="Return information on which labels and synonyms matched the search query?"
        )] = False,
        offset: Annotated[int, Query(
            description="The number of results to skip. Can be used to page through the results of a query.",
            # Offset should be greater than or equal to zero.
            ge=0
        )] = 0,
        limit: Annotated[int, Query(
            description="The number of results to skip. Can be used to page through the results of a query.",
            # Limit should be greater than or equal to zero and less than or equal to 1000.
            ge=0,
            le=1000
        )] = 10,
        biolink_type: Annotated[Union[List[str], None], Query(
            description="The Biolink types to filter to (with or without the `biolink:` prefix), "
                        "e.g. `biolink:Disease` or `Disease`. Multiple types will be combined with OR, i.e. filtering "
                        "for PhenotypicFeature and Disease will return concepts that are either PhenotypicFeatures OR "
                        "Disease, not concepts that are both PhenotypicFeature AND Disease.",
            # We can't use `example` here because otherwise it gets filled in when you click "Try it out",
            # which is easy to overlook.
            # example=["biolink:Disease", "biolink:PhenotypicFeature"]
        )] = [],
        only_prefixes: Annotated[Union[str, None], Query(
            description="Pipe-separated, case-sensitive list of prefixes to filter to, e.g. `MONDO|EFO`.",
            # We can't use `example` here because otherwise it gets filled in when filling this in.
            # example="MONDO|EFO"
        )] = None,
        exclude_prefixes: Annotated[Union[str, None], Query(
            description="Pipe-separated, case-sensitive list of prefixes to exclude, e.g. `UMLS|EFO`.",
            # We can't use `example` here because otherwise it gets filled in when filling this in.
            # example="UMLS|EFO"
        )] = None,
        only_taxa: Annotated[Union[str, None], Query(
            description="Pipe-separated, case-sensitive list of taxa to filter, "
                        "e.g. `NCBITaxon:9606|NCBITaxon:10090|NCBITaxon:10116|NCBITaxon:7955`.",
            # We can't use `example` here because otherwise it gets filled in when filling this in.
            # example="NCBITaxon:9606|NCBITaxon:10090|NCBITaxon:10116|NCBITaxon:7955"
        )] = None
) -> List[LookupResult]:
    """
    Returns cliques with a name or synonym that contains a specified string.
    """
    return await lookup(string, autocomplete, highlighting, offset, limit, biolink_type, only_prefixes, exclude_prefixes, only_taxa)


async def lookup(string: str,
           autocomplete: bool = False,
           highlighting: bool = False,
           offset: int = 0,
           limit: conint(le=1000) = 10,
           biolink_types: List[str] = None,
           only_prefixes: str = "",
           exclude_prefixes: str = "",
           only_taxa: str = ""
) -> List[LookupResult]:
    """
    Returns cliques with a name or synonym that contains a specified string.

    :param autocomplete: Should we do the lookup in autocomplete mode (in which we expect the final word to be
        incomplete) or not (in which the entire phrase is expected to be complete, i.e. as an entity linker)?
    :param highlighting: Return information on which labels and synonyms matched the search query.
    :param biolink_types: A list of Biolink types to filter (with or without the `biolink:` prefix). Note that these are
        additive, i.e. if this list is ['PhenotypicFeature', 'Disease'], then both phenotypic features AND diseases
        will be returned, rather than filtering to concepts that are both PhenotypicFeature and Disease.
    """

    # First, we strip and lowercase the query since all our indexes are case-insensitive.
    string_lc = string.strip().lower()

    # There is a possibility that the input text isn't in UTF-8.
    # We could try a bunch of Python packages to try to determine what the encoding actually is:
    #   - https://pypi.org/project/charset-normalizer/
    #   - https://www.crummy.com/software/BeautifulSoup/bs4/doc/#unicode-dammit
    # But the only issue we've actually run into so far has been the Windows smart
    # quote (https://github.com/TranslatorSRI/NameResolution/issues/176), so for now
    # let's detect and replace just those characters.
    string_lc = re.sub(r"[“”]", '"', re.sub(r"[‘’]", "'", string_lc))

    # Do we have a search string at all?
    if string_lc == "":
        return []

    # For reasons I don't understand, we need to use backslash to escape characters (e.g. "\(") to remove the special
    # significance of characters inside round brackets, but not inside double-quotes. So we escape them separately:
    # - For a full exact search, we only remove double-quotes and slashes, leaving other special characters as-is.
    string_lc_escape_groupings = string_lc.replace('"', '').replace('\\', '')

    # - For a tokenized search, we escape all special characters with backslashes as well as other characters that might
    #   mess up the search.
    string_lc_escape_everything = re.sub(r'([!(){}\[\]^"~*?:/+-\\])', r'\\\g<0>', string_lc) \
        .replace('&&', ' ').replace('||', ' ')

    # If autocomplete mode is turned on, add an asterisk at the end so that we look for incomplete terms.
    if autocomplete:
        query = f'"{string_lc_escape_groupings}" OR ({string_lc_escape_everything}*)'
    else:
        query = f'"{string_lc_escape_groupings}" OR ({string_lc_escape_everything})'

    # Apply filters as needed.
    filters = []

    # Biolink type filter
    if biolink_types:
        biolink_types_filters = []
        for biolink_type in biolink_types:
            biolink_type_stripped = biolink_type.strip()
            if biolink_type_stripped:
                if biolink_type_stripped.startswith('biolink:'):
                    biolink_type_stripped = biolink_type_stripped[8:]
                biolink_types_filters.append(f"types:{biolink_type_stripped}")
        filters.append(" OR ".join(biolink_types_filters))

    # Prefix: only filter
    if only_prefixes:
        prefix_filters = []
        for prefix in re.split('\\s*\\|\\s*', only_prefixes):
            prefix_filters.append(f"curie:/{prefix}:.*/")
        filters.append(" OR ".join(prefix_filters))

    # Prefix: exclude filter
    if exclude_prefixes:
        prefix_exclude_filters = []
        for prefix in re.split('\\s*\\|\\s*', exclude_prefixes):
            prefix_exclude_filters.append(f"NOT curie:/{prefix}:.*/")
        filters.append(" AND ".join(prefix_exclude_filters))

    # Taxa filter.
    # only_taxa is like: 'NCBITaxon:9606|NCBITaxon:10090|NCBITaxon:10116|NCBITaxon:7955'
    if only_taxa:
        taxa_filters = []
        for taxon in re.split('\\s*\\|\\s*', only_taxa):
            taxa_filters.append(f'taxa:"{taxon}"')
        filters.append(" OR ".join(taxa_filters))

    # Turn on highlighting if requested.
    inner_params = {}
    if highlighting:
        inner_params.update({
            # Highlighting
            "hl": "true",
            "hl.method": "unified",
            "hl.encoder": "html",
            "hl.tag.pre": "<strong>",
            "hl.tag.post": "</strong>",
            # "hl.usePhraseHighlighter": "true",
            # "hl.highlightMultiTerm": "true",
        })

    params = {
        "query": {
            "edismax": {
                "query": query,
                # qf = query fields, i.e. how should we boost these fields if they contain the same fields as the input.
                # https://solr.apache.org/guide/solr/latest/query-guide/dismax-query-parser.html#qf-query-fields-parameter
                "qf": "preferred_name_exactish^250 names_exactish^100 preferred_name^25 names^10",
                # pf = phrase fields, i.e. how should we boost these fields if they contain the entire search phrase.
                # https://solr.apache.org/guide/solr/latest/query-guide/dismax-query-parser.html#pf-phrase-fields-parameter
                "pf": "preferred_name_exactish^300 names_exactish^200 preferred_name^30 names^20",
                # Boosts
                "bq": [],
                "boost": [
                    # The boost is multiplied with score -- calculating the log() reduces how quickly this increases
                    # the score for increasing clique identifier counts.
                    # "log(sum(clique_identifier_count, 1))"
                    #
                    # However, this approach causes very large clique_identifier_count entries (like diphenhydramine, cic=1332)
                    # to be returned when we don't have an otherwise good match. So instead we make it stepwise:
                    #   - If clique_identifier_count > 1, we boost by 2x
                    "if(gt(clique_identifier_count,1),2,1)",
                    #   - If clique_identifier_count > 5, we boost by a further 2x
                    "if(gt(clique_identifier_count,5),2,1)",
                ],
            },
        },
        "sort": "score DESC, clique_identifier_count DESC, curie_suffix ASC",
        "limit": limit,
        "offset": offset,
        "filter": filters,
        "fields": "*, score",
        "params": inner_params,
    }
    logging.debug(f"Query: {json.dumps(params, indent=2)}")

    query_url = f"http://{SOLR_HOST}:{SOLR_PORT}/solr/name_lookup/select"
    async with httpx.AsyncClient(timeout=None) as client:
        response = await client.post(query_url, json=params)
    if response.status_code >= 300:
        LOGGER.error("Solr REST error: %s", response.text)
        response.raise_for_status()
    response = response.json()
    logging.debug(f"Solr response: {json.dumps(response, indent=2)}")

    # Associate highlighting information with search results.
    highlighting_response = response.get("highlighting", {})

    outputs = []
    for doc in response['response']['docs']:
        preferred_matches = []
        synonym_matches = []

        if doc['id'] in highlighting_response:
            matches = highlighting_response[doc['id']]

            # We order exactish matches before token matches.
            if 'preferred_name_exactish' in matches:
                preferred_matches.extend(matches['preferred_name_exactish'])
            if 'preferred_name' in matches:
                preferred_matches.extend(matches['preferred_name'])

            # Solr sometimes returns duplicates or a blank string here?
            preferred_matches = list(filter(lambda s: s, set(preferred_matches)))

            # We order exactish matches before token matches.
            if 'names_exactish' in matches:
                synonym_matches.extend(matches['names_exactish'])
            if 'names' in matches:
                synonym_matches.extend(matches['names'])

            # Solr sometimes returns duplicates or a blank string here?
            synonym_matches = list(filter(lambda s: s, set(synonym_matches)))

        outputs.append(LookupResult(curie=doc.get("curie", ""),
                           label=doc.get("preferred_name", ""),
                           highlighting={
                               'labels': preferred_matches,
                               'synonyms': synonym_matches,
                           } if highlighting else {},
                           synonyms=doc.get("names", []),
                           score=doc.get("score", ""),
                           taxa=doc.get("taxa", []),
                           clique_identifier_count=doc.get("clique_identifier_count", 0),
                           types=[f"biolink:{d}" for d in doc.get("types", [])]))

    return outputs

## BULK ENDPOINT

class NameResQuery(BaseModel):
    """
    A request for name resolution.
    """
    strings: List[str] = Field(
        ..., # Ellipsis means field is required
        description="The strings to search for. The returned results will be in a dictionary with these values as keys."
    )
    autocomplete: Optional[bool] = Field(
        False,
        description="Is the input string incomplete (autocomplete=true) or a complete phrase (autocomplete=false)?"
    )
    highlighting: Optional[bool] = Field(
        False,
        description="Return information on which labels and synonyms matched the search query?"
    )
    offset: Optional[int] = Field(
        0,
        description="The number of results to skip. Can be used to page through the results of a query.",
        # Offset should be greater than or equal to zero.
        ge=0
    )
    limit: Optional[int] = Field(
        10,
        description="The number of results to skip. Can be used to page through the results of a query.",
        # Limit should be greater than or equal to zero and less than or equal to 1000.
        ge=0,
        le=1000
    )
    biolink_types: Optional[List[str]] = Field(
        [],
        description="The Biolink types to filter to (with or without the `biolink:` prefix), "
                    "e.g. `biolink:Disease` or `Disease`. Multiple types will be combined with OR, i.e. filtering "
                    "for PhenotypicFeature and Disease will return concepts that are either PhenotypicFeatures OR "
                    "Disease, not concepts that are both PhenotypicFeature AND Disease.",
    )
    only_prefixes: Optional[str] = Field(
        "",
        description="Pipe-separated, case-sensitive list of prefixes to filter to, e.g. `MONDO|EFO`.",
        # We can't use `example` here because otherwise it gets filled in when filling this in.
        # example="MONDO|EFO"
    )
    exclude_prefixes: Optional[str] = Field(
        "",
        description="Pipe-separated, case-sensitive list of prefixes to exclude, e.g. `UMLS|EFO`.",
        # We can't use `example` here because otherwise it gets filled in when filling this in.
        # example="UMLS|EFO"
    )
    only_taxa: Optional[str] = Query(
        "",
        description="Pipe-separated, case-sensitive list of taxa to filter, "
                    "e.g. `NCBITaxon:9606|NCBITaxon:10090|NCBITaxon:10116|NCBITaxon:7955`.",
        # We can't use `example` here because otherwise it gets filled in when filling this in.
        # example="NCBITaxon:9606|NCBITaxon:10090|NCBITaxon:10116|NCBITaxon:7955"
    )


@app.post("/bulk-lookup",
          summary="Look up cliques for a fragment of multiple names or synonyms.",
          description="Returns cliques for each query.",
          response_model=Dict[str, List[LookupResult]],
          tags=["lookup"]
)
async def bulk_lookup(query: NameResQuery) -> Dict[str, List[LookupResult]]:
    result = {}
    for string in query.strings:
        result[string] = await lookup(
            string,
            query.autocomplete,
            query.highlighting,
            query.offset,
            query.limit,
            query.biolink_types,
            query.only_prefixes,
            query.exclude_prefixes,
            query.only_taxa)
    return result


# Override open api schema with custom schema
app.openapi_schema = construct_open_api_schema(app)

# Set up opentelemetry if enabled.
if os.environ.get('OTEL_ENABLED', 'false') == 'true':
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import SERVICE_NAME, Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    # from opentelemetry.sdk.trace.export import ConsoleSpanExporter

    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

    # httpx connections need to be open a little longer by the otel decorators
    # but some libs display warnings of resource being unclosed.
    # these supresses such warnings.
    logging.captureWarnings(capture=True)
    warnings.filterwarnings("ignore", category=ResourceWarning)
    otel_service_name = os.environ.get('SERVER_NAME', 'infores:sri-node-normalizer')
    assert otel_service_name and isinstance(otel_service_name, str)

    otlp_host = os.environ.get("JAEGER_HOST", "http://localhost/").rstrip('/')
    otlp_port = os.environ.get("JAEGER_PORT", "4317")
    otlp_endpoint = f'{otlp_host}:{otlp_port}'
    otlp_exporter = OTLPSpanExporter(endpoint=f'{otlp_endpoint}')
    processor = BatchSpanProcessor(otlp_exporter)
    # processor = BatchSpanProcessor(ConsoleSpanExporter())
    resource = Resource.create(attributes={
        SERVICE_NAME: os.environ.get("JAEGER_SERVICE_NAME", otel_service_name),
    })
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(processor)
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app, tracer_provider=provider, excluded_urls=
                                       "docs,openapi.json")
    HTTPXClientInstrumentor().instrument()
