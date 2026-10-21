"""Expose the maintained public API after initializing its prerequisites in order."""

from importlib import import_module

# Preserve Cognee 1.6.5 initialization order as executable imports.
# Import sorting must not move registration ahead of its prerequisites.
remember = import_module(".remember", __name__).remember
RememberResult = import_module(".remember", __name__).RememberResult
recall = import_module(".recall", __name__).recall
improve = import_module(".improve", __name__).improve
forget = import_module(".forget", __name__).forget
serve = import_module(".serve", __name__).serve
disconnect = import_module(".serve", __name__).disconnect
push = import_module(".push", __name__).push
PushResult = import_module(".push", __name__).PushResult
export = import_module(".export", __name__).export
ExportResult = import_module(".export", __name__).ExportResult
visualize = import_module("cognee.api.v1.visualize", __name__).visualize_graph
