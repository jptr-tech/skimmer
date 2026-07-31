import warnings

warnings.filterwarnings(
    "ignore",
    message=r"GLib\.unix_signal_add_full is deprecated",
    category=DeprecationWarning,
)
warnings.filterwarnings(
    "ignore",
    message=r"'asyncio\.AbstractEventLoopPolicy' is deprecated",
    category=DeprecationWarning,
)
warnings.filterwarnings(
    "ignore",
    message=r"'asyncio\.get_event_loop_policy' is deprecated",
    category=DeprecationWarning,
)
