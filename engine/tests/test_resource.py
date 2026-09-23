"""Resource: claiming, releasing, and revoking a claim on a required resource."""

from __future__ import annotations

import pytest

from flyball.foundation.resource import NotClaimantError, Operator, Resource


def test_revoke_removes_a_resource_the_claimant_actually_holds():
    claimant = Operator("op")
    dep = Resource("dep")
    claimant.add_claim(dep)
    assert dep in claimant._claimed

    claimant.revoke(dep)  # must not raise: dep is genuinely claimed by claimant

    assert dep not in claimant._claimed


def test_revoke_still_refuses_a_resource_the_claimant_does_not_hold():
    claimant = Operator("op")
    other = Resource("other")
    with pytest.raises(NotClaimantError, match="other.*not held by op"):
        claimant.revoke(other)


def test_revoke_with_no_resource_releases_the_claimant_itself():
    claimant = Operator("op")
    dep = Resource("dep")
    claimant.add_claim(dep)

    claimant.revoke()

    assert claimant.claimant is None
