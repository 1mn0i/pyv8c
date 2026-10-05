"""Window's smaller accessory objects; each reports the idle state a headless
page starts in.
"""

from pyv8 import AttributeSpec, IdlSpec, MethodSpec
from pyv8.flag import FlagLocation, FlagReceiverCheck

from ...engine.features import interface
from ...foundation.arguments import MISSING, callback_value, dom_string, missing_error
from ..dom.events import Event, EventTarget
from .media import evaluate


@interface('External', None)
class External:
    __v8_idl_spec__ = IdlSpec(
        methods=(
            MethodSpec(name="AddSearchProvider", callback="fn_AddSearchProvider", length=1),
            MethodSpec(name="IsSearchProviderInstalled", callback="fn_IsSearchProviderInstalled",
                       length=1),
        ),
        constructor_behavior=0,
        to_string_tag="External",
        has_constructor=1,
    )

    #: No provider is registered, so nothing is added and nothing is installed.
    def fn_AddSearchProvider(self, *_extra): return None
    def fn_IsSearchProviderInstalled(self, *_extra): return 0


@interface('StyleMedia', None)
class StyleMedia:
    __v8_idl_spec__ = IdlSpec(
        attributes=(AttributeSpec(name="type", getter="get_type", side_effect=1),),
        methods=(MethodSpec(name="matchMedium", callback="fn_matchMedium", length=1, side_effect=1),),
        constructor_behavior=0,
        to_string_tag="StyleMedia",
        has_constructor=1,
    )

    def __init__(self, window=None, **_):
        self._window = window

    def get_type(self): return "screen"

    def fn_matchMedium(self, query=MISSING, *_extra):
        if query is MISSING:
            raise missing_error('StyleMedia', 'matchMedium', 1, 0, constructor=False)
        return evaluate(dom_string(query), self._window.innerWidth, self._window.innerHeight)


@interface('CrashReportContext', None)
class CrashReportContext:
    __v8_idl_spec__ = IdlSpec(
        methods=(
            MethodSpec(name="delete", callback="fn_delete", length=1),
            MethodSpec(name="initialize", callback="fn_initialize", length=1),
            MethodSpec(name="set", callback="fn_set", length=2),
        ),
        constructor_behavior=0,
        to_string_tag="CrashReportContext",
        has_constructor=1,
    )

    #: Nothing here reports a crash.
    def fn_delete(self, *_extra): return None
    def fn_initialize(self, *_extra): return None
    def fn_set(self, *_extra): return None


@interface('LaunchQueue', None)
class LaunchQueue:
    __v8_idl_spec__ = IdlSpec(
        methods=(MethodSpec(name="setConsumer", callback="fn_setConsumer", length=1),),
        constructor_behavior=0,
        to_string_tag="LaunchQueue",
        has_constructor=1,
    )

    def __init__(self, **_):
        self._consumer = None

    def fn_setConsumer(self, callback=MISSING, *_extra):
        """A consumer is kept, and only called when a launch is consumed."""
        if callback is MISSING:
            raise missing_error('LaunchQueue', 'setConsumer', 1, 0, constructor=False)
        self._consumer = callback_value(callback)


@interface('Viewport', None)
class Viewport:
    __v8_idl_spec__ = IdlSpec(
        attributes=(AttributeSpec(name="segments", getter="get_segments", side_effect=1),),
        constructor_behavior=0,
        to_string_tag="Viewport",
        has_constructor=1,
    )

    #: One segment covering the whole view, split into none.
    def get_segments(self): return []


#: The voices Chrome reports on this platform, in the order ``getVoices`` lists them.
_SPEECH_VOICES = (
    ("Microsoft Huihui - Chinese (Simplified, PRC)", "zh-CN", True, True),
    ("Microsoft Kangkang - Chinese (Simplified, PRC)", "zh-CN", True, False),
    ("Microsoft Yaoyao - Chinese (Simplified, PRC)", "zh-CN", True, False),
)

#: How long Chrome's speech service takes to answer: a page reads the empty list
#: first and hears about the voices after this, which is when Chrome fires them.
_VOICES_READY_SECONDS = 0.27


@interface('SpeechSynthesisVoice', None)
class SpeechSynthesisVoice:
    """One voice the synthesiser can speak with."""

    __v8_idl_spec__ = IdlSpec(
        attributes=(
            AttributeSpec(name="voiceURI", getter="get_voiceURI", side_effect=1),
            AttributeSpec(name="name", getter="get_name", side_effect=1),
            AttributeSpec(name="lang", getter="get_lang", side_effect=1),
            AttributeSpec(name="localService", getter="get_localService", side_effect=1),
            AttributeSpec(name="default", getter="get_default", side_effect=1),
        ),
        constructor_behavior=0,
        to_string_tag="SpeechSynthesisVoice",
        has_constructor=1,
    )

    def __init__(self, name="", lang="", local_service=True, default=False, **_):
        self._name, self._lang = str(name), str(lang)
        self._local_service, self._default = bool(local_service), bool(default)

    #: A voice's URI reads back as its name, which is how Chrome's platform voices look.
    def get_voiceURI(self): return self._name
    def get_name(self): return self._name
    def get_lang(self): return self._lang
    def get_localService(self): return self._local_service
    def get_default(self): return self._default


@interface('SpeechSynthesis', 'EventTarget')
class SpeechSynthesis(EventTarget):
    __v8_idl_spec__ = IdlSpec(
        attributes=(
            AttributeSpec(name="pending", getter="get_pending", side_effect=1),
            AttributeSpec(name="speaking", getter="get_speaking", side_effect=1),
            AttributeSpec(name="paused", getter="get_paused", side_effect=1),
            AttributeSpec(name="onvoiceschanged", getter="get_onvoiceschanged",
                          setter="set_onvoiceschanged"),
        ),
        methods=(
            MethodSpec(name="cancel", callback="fn_cancel"),
            MethodSpec(name="getVoices", callback="fn_getVoices", length=0, side_effect=1),
            MethodSpec(name="pause", callback="fn_pause"),
            MethodSpec(name="resume", callback="fn_resume"),
            MethodSpec(name="speak", callback="fn_speak", length=1),
        ),
        constructor_behavior=0,
        to_string_tag="SpeechSynthesis",
        has_constructor=1,
    )

    def __init__(self, services=None, **_):
        super().__init__(services=services)
        #: None until the service answers, then the voices every later read hands out.
        self._voices = None
        if services is not None:
            services.task_services.schedule(self._voices_ready, _VOICES_READY_SECONDS)

    #: Nothing is spoken: the voices are there to be read, never to be used.
    def get_pending(self): return False
    def get_speaking(self): return False
    def get_paused(self): return False
    def get_onvoiceschanged(self): return self._get_handler("onvoiceschanged")
    def set_onvoiceschanged(self, value): self._set_handler("onvoiceschanged", value)
    def fn_cancel(self, *_extra): return None
    def _voices_ready(self):
        self._voices = [SpeechSynthesisVoice(name=name, lang=lang, local_service=local,
                                             default=default, services=self._services)
                        for name, lang, local, default in _SPEECH_VOICES]
        self.fn_dispatchEvent(Event("voiceschanged"))
    def fn_getVoices(self, *_extra):
        return [] if self._voices is None else list(self._voices)
    def fn_pause(self, *_extra): return None
    def fn_resume(self, *_extra): return None
    def fn_speak(self, utterance=MISSING, *_extra):
        if utterance is MISSING:
            raise missing_error('SpeechSynthesis', 'speak', 1, 0, constructor=False)
        return None


@interface('Notification', 'EventTarget')
class Notification(EventTarget):
    __v8_idl_spec__ = IdlSpec(
        attributes=(AttributeSpec(name="permission", getter="get_permission",
                                  location=FlagLocation.kInterface,
                                  receiver_check=FlagReceiverCheck.kDoNotCheck),
                    AttributeSpec(name="maxActions", getter="get_maxActions",
                                  location=FlagLocation.kInterface,
                                  receiver_check=FlagReceiverCheck.kDoNotCheck),),
        constructor_behavior=1,
        length=1,
        to_string_tag="Notification",
        has_constructor=1,
    )

    #: No user is here to answer the prompt, so the page keeps the initial state.
    @staticmethod
    def get_permission(*_args): return "default"

    #: What Chrome allows a notification to carry.
    @staticmethod
    def get_maxActions(*_args): return 2


@interface('Scheduling', None)
class Scheduling:
    __v8_idl_spec__ = IdlSpec(
        methods=(MethodSpec(name="isInputPending", callback="fn_isInputPending"),),
        constructor_behavior=0,
        to_string_tag="Scheduling",
        has_constructor=1,
    )

    def fn_isInputPending(self, *_extra): return False


@interface('Scheduler', None)
class Scheduler:
    __v8_idl_spec__ = IdlSpec(
        methods=(
            MethodSpec(name="postTask", callback="fn_postTask", length=1),
            MethodSpec(name="yield", callback="fn_yield", length=0),
        ),
        constructor_behavior=0,
        to_string_tag="Scheduler",
        has_constructor=1,
    )

    def __init__(self, services=None, **_):
        self._services = services

    def fn_postTask(self, callback=MISSING, options=None, *_extra):
        if callback is MISSING:
            raise missing_error('Scheduler', 'postTask', 1, 0, constructor=False)
        callback = callback_value(callback)
        return self._on_task(callback)

    def fn_yield(self, *_extra):
        return self._on_task(None)

    def _on_task(self, callback):
        services = self._services
        promise, resolve, reject = services.realm_services.make_promise()

        def run():
            try:
                resolve(callback() if callback is not None else None)
            except BaseException as error:
                reject(services.realm_services.js_error_value(error))

        services.task_services.schedule(run, 0, repeat=False)
        return promise


__all__ = [
    "CrashReportContext", "External", "LaunchQueue", "Scheduler",
    "SpeechSynthesis", "StyleMedia", "Viewport",
]
