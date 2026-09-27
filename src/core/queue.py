import asyncio
import logging

logger = logging.getLogger(__name__)

class ApplicationQueue:
    def __init__(self):
        self.queue = asyncio.Queue()
        self.worker_task = None

    async def add_job(self, application_id: int):
        """Add an application ID to the queue."""
        await self.queue.put(application_id)
        logger.info(f"Added application {application_id} to queue.")

    async def process_queue(self):
        """Worker that processes items from the queue."""
        while True:
            application_id = await self.queue.get()
            try:
                # Simulate application automation processing
                logger.info(f"Processing application {application_id}...")
                await asyncio.sleep(1) # Simulated delay
                logger.info(f"Finished processing application {application_id}.")
            except Exception as e:
                logger.error(f"Error processing application {application_id}: {e}")
            finally:
                self.queue.task_done()

    def start_worker(self):
        """Start the background worker."""
        if self.worker_task is None:
            self.worker_task = asyncio.create_task(self.process_queue())
            logger.info("Application queue worker started.")

    async def stop_worker(self):
        """Stop the background worker."""
        if self.worker_task:
            self.worker_task.cancel()
            try:
                await self.worker_task
            except asyncio.CancelledError:
                logger.info("Application queue worker stopped.")
            self.worker_task = None

# Singleton instance
app_queue = ApplicationQueue()
