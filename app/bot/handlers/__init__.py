from aiogram import Router

from app.bot.handlers.profile import router as profile_router
from app.bot.handlers.start import router as start_router
from app.bot.handlers.subscription import router as subscription_router

router = Router()
router.include_router(start_router)
router.include_router(profile_router)
router.include_router(subscription_router)
