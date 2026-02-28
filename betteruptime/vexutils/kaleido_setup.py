import logging

log = logging.getLogger("red.vex-utils")

try:
    import matplotlib

    matplotlib.use("Agg")

    # Ensure Pillow is available for any image handling
    try:
        from PIL import Image  # type: ignore

        async def kaleido_setup() -> bool:
            """Check that Matplotlib (Agg) backend and Pillow are available.

            Returns True when ready. This keeps the same async API as the original
            kaleido_setup so callers don't need to change.
            """
            log.info("Matplotlib (Agg) and Pillow available for plotting")
            return True

    except ImportError:
        async def kaleido_setup() -> bool:
            log.error("Pillow is required for image output but is not installed")
            return False

except ImportError:

    async def kaleido_setup() -> None:
        raise ImportError("Matplotlib/Pillow is not installed; plotting backend unavailable.")
