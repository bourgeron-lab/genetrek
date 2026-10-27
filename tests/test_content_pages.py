"""The prose pages: the table, the files behind it, and the registration.

Adding a page is one entry in `genetrek.content` plus a markdown file, and the
failure mode of forgetting the file is that the application will not start. So
these check the declaration against the checked-in tree, and pin the one
non-obvious mechanic in the registrar.
"""

from __future__ import annotations

import pytest

from genetrek import paths
from genetrek.config.loader import load_config
from genetrek.content import CONTENT_PAGES
from genetrek.ui import content as content_module


class TestTheDeclaration:
    def test_routes_are_unique(self):
        routes = [page.route for page in CONTENT_PAGES]
        assert len(routes) == len(set(routes))

    def test_slugs_are_unique(self):
        slugs = [page.slug for page in CONTENT_PAGES]
        assert len(slugs) == len(set(slugs))

    @pytest.mark.parametrize("page", CONTENT_PAGES, ids=lambda p: p.slug)
    def test_no_route_collides_with_a_reserved_prefix(self, page):
        """`/api` is the API router's prefix and `/docs` is its Swagger UI.

        A page registered on either would shadow or be shadowed depending on
        mount order, which is the kind of bug that shows up as one endpoint
        mysteriously 404ing.
        """
        assert not page.route.startswith("/api/")
        assert page.route not in {"/api", "/docs", "/openapi.json", "/data"}

    @pytest.mark.parametrize("page", CONTENT_PAGES, ids=lambda p: p.slug)
    def test_the_route_is_absolute_and_has_no_trailing_slash(self, page):
        assert page.route.startswith("/")
        assert page.route == "/" or not page.route.endswith("/")

    @pytest.mark.parametrize("page", CONTENT_PAGES, ids=lambda p: p.slug)
    def test_every_page_has_a_label_and_a_title(self, page):
        assert page.title.strip()
        assert page.nav_label.strip()


class TestTheFilesExist:
    @pytest.mark.parametrize("page", CONTENT_PAGES, ids=lambda p: p.slug)
    def test_the_checked_in_tree_has_every_declared_page(self, page):
        """CI catches this before a deploy; the app would refuse to start."""
        try:
            root = paths.resources_dir()
        except paths.ResourcesNotFoundError:  # pragma: no cover
            pytest.skip("no resources directory available")
        path = paths.content_path(page.slug, root)
        assert path.is_file(), f"missing {path}; the webapp will not start"
        assert path.read_text(encoding="utf-8").strip()


class TestLoading:
    def test_every_declared_page_is_loaded(self, resources):
        config = load_config(resources.root)
        assert set(config.documents) == {page.slug for page in CONTENT_PAGES}

    def test_a_missing_page_stops_the_application(self, resources):
        """Loud at startup rather than a page that renders nothing."""
        paths.content_path(CONTENT_PAGES[0].slug, resources.root).unlink()
        with pytest.raises(SystemExit):
            load_config(resources.root)


class TestRegistration:
    def test_each_route_gets_its_own_page_body(self, resources, monkeypatch):
        """The closure trap.

        `@ui.page(route)` binds the route when the decorator runs, but the body
        reads the page it belongs to when a request arrives. Registered from an
        inline loop body, all four routes would close over the same variable
        and every one of them would serve the last page.
        """
        registered: dict[str, object] = {}

        def fake_page(route, **_kwargs: object):
            def decorate(func):
                registered[route] = func
                return func

            return decorate

        monkeypatch.setattr(content_module.ui, "page", fake_page)
        content_module.register_content_pages(load_config(resources.root))

        assert set(registered) == {page.route for page in CONTENT_PAGES}
        # Distinct closures, not four references to one function.
        assert len({id(func) for func in registered.values()}) == len(CONTENT_PAGES)
