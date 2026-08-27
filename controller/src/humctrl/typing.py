from typing import Annotated

from pydantic import Field

NonNegative = Annotated[float, Field(ge=0.0)]
Positive = Annotated[float, Field(gt=0.0)]
PositiveInt = Annotated[int, Field(gt=0)]
NonZero = Annotated[float, Field(lt=0.0)] | Annotated[float, Field(gt=0.0)]
Normalised = Annotated[float, Field(ge=0.0, le=1.0)]
NormalisedPositive = Annotated[float, Field(gt=0.0, le=1.0)]
Percent = Annotated[float, Field(ge=0.0, le=100.0)]
