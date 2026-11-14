from __future__ import annotations

from .models import RouteRecord


class ProviderRegistry:
    def __init__(self, routes: list[RouteRecord] | None = None):
        self._routes: dict[tuple[str, str], RouteRecord] = {}
        for route in routes or []:
            self.add(route)

    def add(self, route: RouteRecord) -> None:
        self._routes[(route.provider_id, route.model_id)] = route

    def get(self, provider_id: str, model_id: str) -> RouteRecord | None:
        return self._routes.get((provider_id, model_id))

    def list(self) -> list[RouteRecord]:
        return sorted(self._routes.values(), key=lambda r: (r.provider_id, r.model_id))

    def enabled(self) -> list[RouteRecord]:
        return [route for route in self.list() if route.enabled]
