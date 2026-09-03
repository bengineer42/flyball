from __future__ import annotations

from collections.abc import Callable, Container, Iterable
from contextlib import suppress
from enum import Enum
from threading import RLock


class ClaimError(Exception):
    pass


class ClaimGraphError(ClaimError):
    pass


class NotClaimantError(ClaimGraphError):
    def __init__(self, lease: Resource, claimant: Resource) -> None:
        name = lease.claimant_name
        string = f"Resource {lease.name} is not held by {claimant.name}"
        if name is not None:
            string += f", not by {claimant.name}"
        super().__init__(string)


class ClaimCycleError(ClaimGraphError):
    def __init__(self, resource: Resource) -> None:
        super().__init__(f"Claim cycle detected for resource {resource.name}")


class ClaimAlreadyRequiredError(ClaimGraphError):
    def __init__(self, lease: Resource) -> None:
        super().__init__(f"Resource {lease.name} is already required")


class AlreadyClaimedError(ClaimError):
    def __init__(self, lease: Resource, claimant: Resource) -> None:
        super().__init__(
            f"Resource {lease.name} is already held by {lease.claimant and lease.claimant.name}, cannot be claimed by {claimant.name}"
        )


class RootType(Enum):
    OPERATOR = "Operator"
    UNCLAIMED = "Unclaimed"

    @property
    def is_root(self) -> bool:
        return True


class Resource:
    _claimant: Resource | None
    _as_operator: bool = False
    _name: str
    _requires: frozenset[Resource]

    _claimed: set[Resource]

    _on_release: Callable | None = None

    def __init__(
        self,
        name: str,
        requires: Iterable[Resource] | None = None,
        as_operator: bool = False,
    ) -> None:
        self._name = name
        self._requires = frozenset(requires) if requires is not None else frozenset()
        self._claimed = set()

    @property
    def name(self) -> str:
        return self._name

    @property
    def claimant(self) -> Resource | None:
        return self._claimant if isinstance(self._claimant, Resource) else None

    @property
    def claimant_name(self) -> str | None:
        return self._claimant and self._claimant.name

    @property
    def operator(self) -> Resource | None:
        if isinstance(self._claimant, Resource):
            return self._claimant.operator
        elif self._claimant is RootType.OPERATOR:
            return self
        return None

    @property
    def operator_name(self) -> str | None:
        operator = self.operator
        return operator and operator.name

    @property
    def ancestors(self) -> set[Resource]:
        claimant = self.claimant
        if claimant:
            ancestors = claimant.ancestors
            self.raise_if_in(ancestors, ClaimCycleError)
            ancestors.add(claimant)
            return ancestors
        return set()

    @property
    def required_descendants(self) -> set[Resource]:
        seen: set[Resource] = set()
        self._required_descendants(seen, set())
        return seen

    @property
    def is_root(self) -> bool:
        return isinstance(self._claimant, RootType)

    @property
    def is_operator(self) -> bool:
        return self._claimant is RootType.OPERATOR

    @property
    def requirements_open(self) -> bool:
        return all(resource.claimant is None for resource in self.required_descendants)

    def _required_descendants(self, seen: set[Resource], parents: set[Resource]) -> None:
        self.raise_if_in(parents, ClaimCycleError)
        self.raise_if_in(seen, ClaimAlreadyRequiredError)
        parents.add(self)
        for resource in self._requires:
            resource._required_descendants(seen, parents)
            seen.add(resource)
        parents.remove(self)

    def validate_requirements(self):
        self._required_descendants(set(), set())

    def raise_if_in(self, nodes: Container[Resource], error: type[ClaimError], *args) -> None:
        if self in nodes:
            raise error(self, *args)

    def raise_if_not_in(self, nodes: Container[Resource], error: type[ClaimError], *args) -> None:
        if self not in nodes:
            raise error(self, *args)

    def append_exception_if_not_in(
        self,
        nodes: Container[Resource],
        exceptions: list[Exception],
        error: type[ClaimError],
        *args,
    ) -> None:
        if self not in nodes:
            exceptions.append(error(self, *args))

    def attach_on_claim(self, callback: Callable[[], None]) -> None:
        self._on_claim = callback

    def remove_on_claim(self) -> None:
        self._on_claim = None

    def attach_on_release(self, callback: Callable[[], None]) -> None:
        self._on_release = callback

    def remove_on_release(self) -> None:
        self._on_release = None

    def add_claim(self, resource: Resource) -> None:
        resource.raise_if_in(self._claimed, ClaimAlreadyRequiredError, self)
        resource.claim(self)
        self._claimed.add(resource)

    def claim(self, claimant: Resource) -> None:
        if old := self.claimant:
            old.revoke(self)
        else:
            for resource in self._requires:
                resource.add_claim(self)
        self._claimant = claimant

    def on_graph_error(self, error: ClaimGraphError) -> None:
        with suppress(ClaimGraphError):
            if claimant := self.claimant:
                claimant.revoke(self)
        self.release()
        raise error

    def revoke(self, resource: Resource | None = None) -> None:

        if resource is not None:
            self.raise_if_not_in(self._claimed, NotClaimantError, self)
            self._claimed.remove(resource)
        if claimant := self.claimant:
            claimant.revoke(self)
        self.release()

    def _revoke(self, resource: Resource, exceptions: list[Exception]) -> None:
        if resource is not None:
            self.raise_if_not_in(self._claimed, NotClaimantError, self)
            self._claimed.remove(resource)
        if claimant := self.claimant:
            claimant.revoke(self)
        self.release()

    def try_on_release(self) -> Exception | None:
        try:
            if self._on_release:
                self._on_release()
        except Exception as e:
            return e
        return None

    def release(self) -> None:
        self._claimant = None
        self.try_on_release()
        for claim in self._claimed:
            claim.release()
        self._claimant = None
        self._claimed.clear()

    def as_operator(self):

    def take(self, resource: Resource) -> None:
        if resource not in self._claimed:
            raise NotClaimantError(self, resource)
        self._claimed.remove(resource)
        self.release()


class Arbiter:
    _resources: dict[str, Resource]
    _operators: dict[str, Resource]
    lock: RLock

    def __init__(self) -> None:
        self._resources = {}
        self.lock = RLock()

    def add(self, lease: Resource) -> None:
        self._resources[lease.name] = lease

    def get(self, name: str) -> Resource | None:
        return self._resources.get(name)


FlowResource = Resource("flow")
FractionResource = Resource("fraction")

PumpsResource = Resource("pumps", [FlowResource, FractionResource])

ControllerResource = Resource("controller", [FractionResource])
