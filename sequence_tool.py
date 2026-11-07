"""Opt-in bounded sequences of upstream input tools under one control lease.

Call install() before windows_mcp.__main__ builds its server. This module never
creates a controller lease: the upstream ControlToolGate must own that lease.
"""
import inspect
import sys
import time

MAX_ACTIONS = 16
MAX_TEXT = 4000
ALLOWED = {
    'Click': {'loc', 'button', 'clicks'},
    'Type': {'loc', 'text', 'clear', 'caret_position', 'press_enter'},
    'Shortcut': {'shortcut'},
}


def validate(actions):
    """Validate the complete plan before executing its first action."""
    if not isinstance(actions, list) or not 1 <= len(actions) <= MAX_ACTIONS:
        raise ValueError(f'actions must contain 1 to {MAX_ACTIONS} entries')
    prepared = []
    total_text = 0
    for index, action in enumerate(actions):
        if not isinstance(action, dict) or set(action) != {'name', 'arguments'}:
            raise ValueError(f'action {index} must contain name and arguments only')
        name, arguments = action['name'], action['arguments']
        if not isinstance(name, str) or name not in ALLOWED:
            raise ValueError(f'action {index} must use Click, Type, or Shortcut')
        if not isinstance(arguments, dict) or set(arguments) - ALLOWED[name]:
            raise ValueError(f'action {index} contains unsupported arguments')
        args = dict(arguments)
        if name in ('Click', 'Type'):
            loc = args.get('loc')
            if (not isinstance(loc, list) or len(loc) != 2
                    or any(type(v) is not int for v in loc)):
                raise ValueError(f'action {index} requires loc=[x,y] with integer coordinates')
            args['loc'] = list(loc)
        if name == 'Click':
            if args.get('button', 'left') not in ('left', 'right', 'middle'):
                raise ValueError(f'action {index} has an invalid button')
            if type(args.get('clicks', 1)) is not int or args.get('clicks', 1) not in (0, 1, 2):
                raise ValueError(f'action {index} has an invalid click count')
        elif name == 'Type':
            if not isinstance(args.get('text'), str):
                raise ValueError(f'action {index} requires text')
            try:
                args['text'].encode('utf-16-le', errors='strict')
            except UnicodeEncodeError:
                raise ValueError(f'action {index} contains invalid UTF-16 text') from None
            total_text += len(args['text'])
            for key in ('clear', 'press_enter'):
                if key in args and type(args[key]) is not bool:
                    raise ValueError(f'action {index} requires a boolean {key}')
            if args.get('caret_position', 'idle') not in ('start', 'idle', 'end'):
                raise ValueError(f'action {index} has an invalid caret_position')
        elif not isinstance(args.get('shortcut'), str) or not 1 <= len(args['shortcut']) <= 100:
            raise ValueError(f'action {index} requires a shortcut of 1 to 100 characters')
        prepared.append((name, args))
    if total_text > MAX_TEXT:
        raise ValueError(f'sequence text must total at most {MAX_TEXT} characters')
    return prepared


async def execute(actions, tools, checkpoint, max_seconds=30.0, clock=time.monotonic):
    """Run once, in order. Never retry input or continue after a failed action.

    max_seconds bounds starting another action, not the duration of an already
    running native operation. Cancellation and input release remain upstream.
    """
    if (type(max_seconds) not in (int, float) or not 0 < max_seconds <= 30):
        raise ValueError('max_seconds must be greater than zero and at most 30')
    prepared = validate(actions)
    for name, arguments in prepared:
        # Catch incompatible upstream signatures before any input occurs.
        inspect.signature(tools[name]).bind(**arguments)
    start = clock()
    completed = []
    for index, (name, arguments) in enumerate(prepared):
        checkpoint()
        if clock() - start >= max_seconds:
            raise TimeoutError(f'Sequence stopped before action {index}; {index} actions completed; do not blindly retry')
        before = clock()
        try:
            value = tools[name](**arguments)
            if inspect.isawaitable(value):
                await value
        except Exception as exc:
            # Preserve ControlBlocked/InputUnavailable types for upstream handling.
            exc.add_note(f'Sequence failed at action {index}; {index} prior actions completed. No retries performed.')
            raise
        completed.append({'index': index, 'name': name, 'seconds': round(clock() - before, 4)})
    checkpoint()
    return {'completed': len(completed), 'seconds': round(clock() - start, 4), 'actions': completed,
            'verification': 'Input calls completed. Observe the UI to verify resulting state.'}


async def fill_form(loc, fields, type_tool, checkpoint, submit=False):
    """Send values to an observed empty form with a verified linear tab order.

    The upstream Type wrapper retains UIA threading and controller checkpoints.
    The native input profile may optimize the resulting tab-delimited text.
    """
    if (not isinstance(loc, list) or len(loc) != 2
            or any(type(value) is not int for value in loc)):
        raise ValueError('loc must be [x,y] with integer coordinates')
    if not isinstance(fields, list) or not 1 <= len(fields) <= MAX_ACTIONS:
        raise ValueError(f'fields must contain 1 to {MAX_ACTIONS} text values')
    if type(submit) is not bool:
        raise ValueError('submit must be a boolean')
    total = 0
    for index, value in enumerate(fields):
        if not isinstance(value, str):
            raise ValueError(f'field {index} must be text')
        if any(char in value for char in ('\t', '\r', '\n', '\0')):
            raise ValueError(f'field {index} must not contain tab, newline, or NUL characters')
        try:
            value.encode('utf-16-le', errors='strict')
        except UnicodeEncodeError:
            raise ValueError(f'field {index} contains invalid UTF-16 text') from None
        total += len(value)
    if total > MAX_TEXT:
        raise ValueError(f'fields must total at most {MAX_TEXT} characters')
    arguments = {'loc': list(loc), 'text': '\t'.join(fields) + ('\t' if submit else ''),
                 'clear': False, 'press_enter': submit}
    inspect.signature(type_tool).bind(**arguments)
    checkpoint()
    result = type_tool(**arguments)
    if inspect.isawaitable(result):
        await result
    checkpoint()
    return {'fields_sent': len(fields), 'submit_requested': submit,
            'verification': 'Input completed. Observe the form to verify values and submission state.'}


def register(mcp, *, get_desktop, get_analytics):
    """Participate in upstream registration; reuse its actual tool wrappers."""
    from mcp.types import ToolAnnotations
    from windows_mcp.tools import input as upstream_input
    from windows_mcp.desktop.control import get_controller

    tools = {}

    class Collector:
        def tool(self, **metadata):
            def capture(function):
                if metadata['name'] in ALLOWED:
                    tools[metadata['name']] = function
                return function
            return capture

    upstream_input.register(Collector(), get_desktop=get_desktop, get_analytics=get_analytics)
    if set(tools) != set(ALLOWED):
        raise RuntimeError('Unsupported Windows MCP input API; Sequence not registered')

    @mcp.tool(name='Sequence', description=(
        'QuickControl: execute 1-16 known stable input actions in one call. '
        'actions=[{"name":"Type","arguments":{"loc":[100,200],"text":"hello"}},'
        '{"name":"Shortcut","arguments":{"shortcut":"tab"}}]. '
        'Only Click, Type and Shortcut; coordinates only, no labels. Use after observing '
        'the current UI. Never span navigation, dialogs, changing layouts, or steps requiring '
        'an intermediate decision. Stops on first failure, no retries or rollback. '
        'The time budget only prevents starting more actions; native in-flight actions '
        'are not forcibly timed out. Observe after completion to verify results.'),
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True,
                                    idempotentHint=False, openWorldHint=False))
    async def sequence(actions: list[dict], max_seconds: float = 30.0) -> dict:
        return await execute(actions, tools, get_controller().checkpoint_current, max_seconds)

    @mcp.tool(name='FillForm', description=(
        'QuickControl: fill an already observed EMPTY form with 1-16 text fields in '
        'verified keyboard tab order. loc=[x,y] is the first empty field; fields is a '
        'list of strings (at most 4000 total characters), with no embedded tabs or '
        'newlines. Sends all values through the original Type wrapper with tabs '
        'between fields, without clearing existing content. Default submit=False '
        'leaves focus on the last field. Set submit=True ONLY after observing that '
        'the next Tab focuses the intended Save/Submit control: adds one trailing '
        'Tab and presses Enter. Do not use for unknown tab order, existing values, '
        'dropdowns, dynamic fields, multiline inputs, or intermediate decisions. '
        'No automatic retries or rollback. Observe afterward to verify the result.'),
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True,
                                    idempotentHint=False, openWorldHint=False))
    async def fill_form_tool(loc: list[int], fields: list[str], submit: bool = False) -> dict:
        return await fill_form(loc, fields, tools['Type'],
                               get_controller().checkpoint_current, submit=submit)


def install():
    """Opt in before server creation; keep the existing control middleware."""
    from windows_mcp import tools
    modules = getattr(tools, '_MODULES', None)
    if not isinstance(modules, list):
        raise RuntimeError('Unsupported Windows MCP registration API')
    module = sys.modules[__name__]
    if module not in modules:
        modules.append(module)
