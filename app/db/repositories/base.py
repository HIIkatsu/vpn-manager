from typing import Generic, TypeVar

from sqlalchemy import Select, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

ModelT = TypeVar("ModelT")


class BaseRepository(Generic[ModelT]):
    model: type[ModelT]

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(self, obj_id: int) -> ModelT | None:
        return await self.session.get(self.model, obj_id)

    async def list_all(self) -> list[ModelT]:
        result = await self.session.scalars(select(self.model))
        return list(result.all())

    async def add(self, obj: ModelT) -> ModelT:
        self.session.add(obj)
        await self.session.flush()
        return obj

    async def delete_by_id(self, obj_id: int) -> bool:
        result = await self.session.execute(delete(self.model).where(self.model.id == obj_id))
        return result.rowcount > 0

    async def execute_scalar(self, stmt: Select[tuple[ModelT]]) -> ModelT | None:
        return await self.session.scalar(stmt)
