import asyncio
import logging

from app.scheduler.reminders import send_due_reminders

logger = logging.getLogger("clinic.scheduler.reminders_once")


async def main() -> None:
    logger.info("Reminder scheduler (once) started")
    await send_due_reminders()
    logger.info("Reminder scheduler (once) finished")


if __name__ == "__main__":
    asyncio.run(main())
