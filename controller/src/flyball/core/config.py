from abc import ABC, abstractmethod
from typing import overload

from pydantic import BaseModel


class Config[T](BaseModel, ABC):
    @abstractmethod
    def build(self) -> T: ...


type ConfigOr[T] = Config[T] | T


@overload
def resolve[T](config: Config[T]) -> T: ...
@overload
def resolve[T](config: Config[T] | None) -> T | None: ...
@overload
def resolve[T](config: ConfigOr[T]) -> T: ...
def resolve[T](config: Config[T] | T | None) -> T | None:
    if isinstance(config, Config):
        return config.build()
    return config
