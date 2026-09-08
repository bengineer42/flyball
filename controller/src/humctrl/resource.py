from __future__ import annotations

from collections.abc import Callable, Container, Iterable, Iterator
from contextlib import suppress
from enum import Enum
from threading import RLock

from humctrl.error import ConflictError, HumCtrlError, NotFoundError


class ClaimError(HumCtrlError):
    """Base for everything the resource graph raises."""


class ClaimGraphError(ClaimError):
    """The claim graph itself is malformed: a cycle, or a double requirement."""


class NotClaimantError(ClaimGraphError, ConflictError):
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


class AlreadyClaimedError(ClaimError, ConflictError):
    def __init__(self, lease: Resource, claimant: Resource) -> None:
        super().__init__(
            f"Resource {lease.name} is already held by "
            f"{lease.claimant and lease.claimant.name}, "
            f"cannot be claimed by {claimant.name}"
        )


class ResourceDoesNotExistError(ClaimError, NotFoundError):
    def __init__(self, resource_name: str, claimant: Operator | None = None) -> None:
        string = f"Resource {resource_name} does not exist"
        if claimant is not None:
            string += f"so cannot be claimed by {claimant.name}"
        super().__init__(string)


# class RootType(Enum):
#     OPERATOR = "Operator"
#     UNCLAIMED = "Unclaimed"

#     @property
#     def is_root(self) -> bool:
#         return True


class ReleaseReason(Enum):
    RELEASED = "released"
    REVOKED = "revoked"


class Resource:
    __slots__ = (
        "_as_operator",
        "_claimant",
        "_claimed",
        "_name",
        "_on_idle",
        "_on_revoke",
        "_requires",
    )
    _claimant: Resource | None
    _name: str
    _requires: frozenset[Resource]

    _claimed: set[Resource]

    _on_revoke: Callable | None
    _on_idle: Callable | None

    def __init__(
        self,
        name: str,
        requires: Iterable[Resource] | None = None,
        on_idle: Callable | None = None,
        on_revoke: Callable | None = None,
    ) -> None:
        self._name = name
        self._requires = frozenset(requires) if requires is not None else frozenset()
        self._claimed = set()
        self._on_revoke = on_revoke
        self._on_idle = on_idle
        self._claimant = None
        self.validate_requirements()

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

    def attach_on_revoke(self, callback: Callable[[], None]) -> None:
        self._on_revoke = callback

    def attach_on_idle(self, callback: Callable[[], None]) -> None:
        self._on_idle = callback

    def remove_on_revoke(self) -> None:
        self._on_revoke = None

    def remove_on_idle(self) -> None:
        self._on_idle = None

    def add_claim(self, resource: Resource) -> None:
        resource.raise_if_in(self._claimed, ClaimAlreadyRequiredError, self)
        resource.claim(self)
        self._claimed.add(resource)

    def drop(self, resource: Resource) -> None:
        resource.raise_if_not_in(self._claimed, NotClaimantError, self)
        self._claimed.remove(resource)

    def claim(self, claimant: Resource | None) -> None:
        if claimant is self.claimant:
            return

        if old := self.claimant:
            self.try_on_revoke()
            old.drop(self)
            old.revoke(self)
        else:
            for resource in self._requires:
                self.add_claim(resource)
        self._claimant = claimant

    def on_graph_error(self, error: ClaimGraphError) -> None:
        with suppress(ClaimGraphError):
            if claimant := self.claimant:
                claimant.revoke(self)
        self.release(ReleaseReason.REVOKED)
        raise error

    def revoke(self, resource: Resource | None = None) -> None:
        if resource is not None:
            self.raise_if_not_in(self._claimed, NotClaimantError, self)
            self._claimed.remove(resource)
        if claimant := self.claimant:
            claimant.revoke(self)
        self.release(ReleaseReason.REVOKED)

    def try_on_revoke(self) -> Exception | None:
        try:
            if self._on_revoke:
                self._on_revoke()
        except Exception as e:
            return e
        return None

    def try_on_idle(self) -> Exception | None:
        try:
            if self._on_idle:
                self._on_idle()
        except Exception as e:
            return e
        return None

    def release(self, reason: ReleaseReason = ReleaseReason.RELEASED) -> None:
        self._claimant = None
        if reason == ReleaseReason.REVOKED:
            self.try_on_revoke()

        for claim in self._claimed:
            claim.release(reason)
        self._claimant = None
        self._claimed.clear()


class Operator(Resource):
    @property
    def operator(self) -> Resource | None:
        return self


class Arbiter:
    _resources: dict[str, Resource]
    _operators: dict[str, Operator]
    lock: RLock

    def __init__(self) -> None:
        self._resources = {}
        self._operators = {}
        self.lock = RLock()

    def add_resource(self, lease: Resource) -> None:
        with self.lock:
            self._add(lease)

    def add_resources(self, leases: Iterator[Resource]) -> None:
        with self.lock:
            for lease in leases:
                self._add(lease)

    def _add(self, resource: Resource) -> None:
        self._resources[resource.name] = resource

    def claim(self, claimant: Operator, resource: str) -> None:
        with self.lock:
            if _resource := self._resources.get(resource):
                _resource.claim(claimant)
            else:
                raise ResourceDoesNotExistError(resource, claimant)
            self._operators[claimant.name] = claimant
