from enum import Enum

from humctrl.resource import Resource

FlowResource = Resource("flow")
FractionResource = Resource("fraction")

PumpsResource = Resource("pumps", [FlowResource, FractionResource])
BlendResource = Resource("blend", [FlowResource, FractionResource])

ControllerResource = Resource("controller", [FractionResource])

SetPointResource = Resource("setpoint", [ControllerResource])

SetPointProfileResource = Resource("setpoint_profile", [SetPointResource])


class PumpsClaim(Enum):
    FLOW = "flow"
    FRACTION = "fraction"
    PUMPS = "pumps"

    def resource(self) -> Resource:
        if self == PumpsClaim.FLOW:
            return FlowResource
        elif self == PumpsClaim.FRACTION:
            return FractionResource
        else:
            return PumpsResource

    def claim(self, claimant: Resource) -> Resource:
        resource = self.resource()
        resource.claim(claimant)
        return resource
