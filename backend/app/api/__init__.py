"""API layer — REST routers.

``app.main.create_app`` imports the router submodules it mounts explicitly. This package
``__init__`` intentionally does **not** eagerly import the routers: doing so makes any import of
``app.api.schemas.*`` pull in every router, which created a circular import
``domain.scheduler`` -> ``api.schemas.session`` -> ``api`` __init__ -> ``sessions_router`` ->
``domain.scheduler`` that crashed the worker process. Routers are imported directly where needed.
"""
