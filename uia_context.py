"""Preserve ownership context and drain cooperative UIA cancellation safely."""
import asyncio
import contextvars
import functools
import sys
import threading


class InputCancelled(RuntimeError):
    """This input invocation was cancelled; do not inject another step."""


_cancel_event = contextvars.ContextVar('quickcontrol_input_cancel_event', default=None)


def check_cancelled():
    """Native input checkpoints call this before beginning another step."""
    event = _cancel_event.get()
    if event is not None and event.is_set():
        raise InputCancelled('Input request was cancelled; input was not retried')


def build_runner(executor, is_uia_thread):
    """Build a bridge around the existing COM-affine worker, never a new lane."""
    async def run_uia_affine(func, *args, **kwargs):
        if is_uia_thread():
            check_cancelled()
            return func(*args, **kwargs)

        # ContextVars do not cross run_in_executor by themselves. Each invocation
        # gets its own event, so cancelling one call cannot block another call.
        context = contextvars.copy_context()
        event = threading.Event()
        context.run(_cancel_event.set, event)

        def invoke():
            check_cancelled()
            return func(*args, **kwargs)

        future = asyncio.get_running_loop().run_in_executor(executor, context.run, invoke)
        try:
            return await asyncio.shield(future)
        except asyncio.CancelledError:
            event.set()
            # Do not let middleware release its control gate while this native
            # worker can still inject. It cooperatively stops at the next input
            # checkpoint; an in-flight native API is allowed to finish.
            while not future.done():
                try:
                    await asyncio.shield(future)
                except asyncio.CancelledError:
                    event.set()  # Repeated cancellation still must drain.
                except Exception:
                    break
            if future.done() and not future.cancelled():
                future.exception()  # Retrieve worker error, preserving cancellation.
            raise

    return run_uia_affine


def install():
    """Patch the upstream worker entry point and already-imported aliases."""
    from windows_mcp.infrastructure import thread_affinity

    original = thread_affinity.run_uia_affine
    if getattr(original, '_quickcontrol_context', False):
        return original
    executor = getattr(thread_affinity, '_executor', None)
    is_uia_thread = getattr(thread_affinity, 'is_uia_thread', None)
    if executor is None or not callable(is_uia_thread):
        raise RuntimeError('Unsupported Windows MCP UIA worker API')
    adapted = functools.wraps(original)(build_runner(executor, is_uia_thread))
    adapted._quickcontrol_context = True
    # analytics imports the function directly, as does infrastructure.__init__.
    # Update only aliases that still reference this exact upstream function.
    for name in ('windows_mcp.infrastructure.thread_affinity',
                 'windows_mcp.infrastructure', 'windows_mcp.infrastructure.analytics'):
        module = sys.modules.get(name)
        if module is not None and getattr(module, 'run_uia_affine', None) is original:
            module.run_uia_affine = adapted
    return adapted
