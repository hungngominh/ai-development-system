"""Command modules. Importing this package triggers registration of all commands."""
# Import each command module to trigger @command decoration side-effects.
# Order matters only for noun registration (first import creates the sub-app).
from ai_dev_system.cli.commands import setup  # noqa: F401
from ai_dev_system.cli.commands import legacy  # noqa: F401
from ai_dev_system.cli.commands import intake  # noqa: F401
from ai_dev_system.cli.commands import migrate  # noqa: F401
from ai_dev_system.cli.commands import eval as _eval  # noqa: F401
from ai_dev_system.cli.commands import phase_b  # noqa: F401
from ai_dev_system.cli.commands import golden  # noqa: F401
from ai_dev_system.cli.commands import gate  # noqa: F401
from ai_dev_system.cli.commands import info  # noqa: F401
