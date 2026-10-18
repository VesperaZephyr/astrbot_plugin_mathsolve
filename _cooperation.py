"""Small, dependency-free contracts shared by the independently packaged plugins."""
import asyncio
import functools
import sys
import types
import weakref
from contextlib import asynccontextmanager
from contextvars import ContextVar


def _shared_state():
    key = '_astrbot_qqbot_cooperation_v1'
    if key not in sys.modules:
        state = types.ModuleType(key)
        state.heavy_locks = weakref.WeakKeyDictionary()
        state.owner = ContextVar(key + '_owner', default=None)
        sys.modules[key] = state
    return sys.modules[key]


@asynccontextmanager
async def heavy_slot():
    """Serialize expensive jobs across plugins, allowing nesting in the same task."""
    state = _shared_state()
    task = asyncio.current_task()
    if state.owner.get() is task:
        yield
        return
    loop = asyncio.get_running_loop()
    lock = state.heavy_locks.get(loop)
    if lock is None:
        lock = asyncio.Semaphore(1)
        state.heavy_locks[loop] = lock
    async with lock:
        token = state.owner.set(task)
        try:
            yield
        finally:
            state.owner.reset(token)


def heavy_job(func):
    @functools.wraps(func)
    async def wrapped(*args, **kwargs):
        async with heavy_slot():
            return await func(*args, **kwargs)
    return wrapped


async def run_worker(func, *args):
    """Keep the resource permit until a cancelled worker has actually finished."""
    job = asyncio.create_task(asyncio.to_thread(func, *args))
    try:
        return await asyncio.shield(job)
    except asyncio.CancelledError:
        while not job.done():
            try:
                await asyncio.shield(job)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if job.done() and not job.cancelled():
            job.exception()
        raise


async def close_resources(*resources):
    for resource in resources:
        if resource is not None:
            try:
                await asyncio.wait_for(resource.close(), timeout=5)
            except Exception:
                pass


def render_job(close_browser):
    """Close browser/driver on every path and retry a dead browser once."""
    def decorate(func):
        @functools.wraps(func)
        async def wrapped(*args, **kwargs):
            async with heavy_slot():
                for attempt in range(2):
                    try:
                        return await func(*args, **kwargs)
                    except Exception as exc:
                        closed = any(marker in str(exc).lower() for marker in (
                            'target page, context or browser has been closed',
                            'browser has been closed', 'browser closed', 'connection closed',
                        ))
                        if attempt or not closed:
                            raise
                    finally:
                        await close_browser()
        return wrapped
    return decorate


def is_command_message(event, text=None):
    text = str(text if text is not None else getattr(event, 'message_str', '') or '').strip()
    if not text:
        return False
    if text.startswith(('/', '!', '！')):
        return True
    if not getattr(event, 'is_at_or_wake_command', False):
        return False
    try:
        from astrbot.core.star.star_handler import EventType, star_handlers_registry
        from astrbot.core.star.filter.command import CommandFilter
        normalized = ' '.join(text.split()).casefold()
        for handler in star_handlers_registry.get_handlers_by_event_type(EventType.AdapterMessageEvent):
            for flt in getattr(handler, 'event_filters', ()):
                if isinstance(flt, CommandFilter):
                    for command in flt.get_complete_command_names():
                        cmd = str(command).casefold()
                        if normalized == cmd or normalized.startswith(cmd + ' '):
                            return True
    except (AttributeError, ImportError):
        pass
    return False


def split_text(text, max_chars=1800):
    """Bound even a single long paragraph, counting separators in the limit."""
    max_chars = max(1, int(max_chars))
    chunks = []
    current = ''
    for paragraph in str(text or '').split('\n\n'):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        while len(paragraph) > max_chars:
            if current:
                chunks.append(current)
                current = ''
            chunks.append(paragraph[:max_chars])
            paragraph = paragraph[max_chars:]
        if not paragraph:
            continue
        joined = current + ('\n\n' if current else '') + paragraph
        if len(joined) > max_chars:
            chunks.append(current)
            current = paragraph
        else:
            current = joined
    if current:
        chunks.append(current)
    return chunks or ['']


async def resolve_provider_id(context, umo=None, configured=''):
    if configured and context.get_provider_by_id(configured) is not None:
        return configured
    if umo:
        try:
            current = await context.get_current_chat_provider_id(umo=umo)
            if current:
                return current
        except Exception:
            pass
    for provider in context.get_all_providers() or ():
        if getattr(provider, 'text_chat', None) is not None:
            return provider.meta().id
    raise RuntimeError('未找到可用的对话模型')


def serialized_state(func):
    """Protect a plugin's read/modify/write KV transactions (including polling)."""
    if hasattr(func, '__code__') and bool(func.__code__.co_flags & 0x200):
        @functools.wraps(func)
        async def gen(self, *args, **kwargs):
            if not hasattr(self, '_state_lock'):
                self._state_lock = asyncio.Lock()
            async with self._state_lock:
                async for item in func(self, *args, **kwargs):
                    yield item
        return gen
    @functools.wraps(func)
    async def coro(self, *args, **kwargs):
        if not hasattr(self, '_state_lock'):
            self._state_lock = asyncio.Lock()
        async with self._state_lock:
            return await func(self, *args, **kwargs)
    return coro


def event_tool_serialized(namespace):
    """Prevent parallel agent tool calls from sending the same article twice."""
    def decorate(func):
        @functools.wraps(func)
        async def wrapped(self, event, *args, **kwargs):
            key = '_cooperation_tool_lock_' + namespace
            lock = event.get_extra(key)
            if lock is None:
                lock = asyncio.Lock()
                event.set_extra(key, lock)
            async with lock:
                return await func(self, event, *args, **kwargs)
        return wrapped
    return decorate
