"""
Open API configuration
"""
import os

from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Dict

from yaml import load, SafeLoader
from fastapi.openapi.utils import get_openapi


@lru_cache(maxsize=1)
def _parse_api_docs() -> Dict:
    """
    Parse openapi.yml. Cached, since the file can't change under a running process and
    get_app_info() is called on every /status request.
    """
    with open(Path(__file__).parent / 'resources' / 'openapi.yml', 'r') as apd_file:
        return load(apd_file, Loader=SafeLoader)


def load_api_docs() -> Dict:
    """
    A fresh copy of the parsed openapi.yml.

    Callers mutate what they take out of this -- construct_open_api_schema() rewrites the
    servers block from the environment -- so they must not share the cached parse, or one
    call would edit a document already handed to someone else.
    """
    return deepcopy(_parse_api_docs())


def get_app_info() -> Dict[str, str]:
    """
    Get title, version, description from openapi.yml
    """
    api_docs = load_api_docs()

    return {
        k : v for k,v in api_docs['info'].items() if k in [
            'title',
            'version',
            'description'
        ]
    }


def construct_open_api_schema(app) -> Dict[str, str]:
    """
    Constructs open api schema
    https://fastapi.tiangolo.com/advanced/extending-openapi/
    """

    api_docs = load_api_docs()

    open_api_schema = get_openapi(
        title=api_docs['info']['title'],
        version=api_docs['info']['version'],
        routes=app.routes
    )

    if 'tags' in api_docs:
        open_api_schema['tags'] = api_docs['tags']

    if 'x-translator' in api_docs['info']:
        open_api_schema['info']['x-translator'] = api_docs['info']['x-translator']

    if 'contact' in api_docs['info']:
        open_api_schema['info']['contact'] = api_docs['info']['contact']

    if 'license' in api_docs['info']:
        open_api_schema['info']['license'] = api_docs['info']['license']

    if 'termsOfService' in api_docs['info']:
        open_api_schema['info']['termsOfService'] = api_docs['info']['termsOfService']

    if 'description' in api_docs['info']:
        open_api_schema['info']['description'] = api_docs['info']['description']

    # adds support to override server root path
    server_root = os.environ.get('SERVER_ROOT', '/')

    # make sure not to add double slash at the end.
    server_root = server_root.rstrip('/') + '/'

    if 'servers' in api_docs:
        for s in api_docs['servers']:
            # override if server root env var is provided
            s['url'] = server_root if server_root != '/' else s['url']
            s['x-maturity'] = os.environ.get("MATURITY_VALUE", "maturity")
            s['x-location'] = os.environ.get("LOCATION_VALUE", "location")
        open_api_schema['servers'] = api_docs['servers']


    return open_api_schema
