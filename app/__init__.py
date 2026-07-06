"""Application composition root: config, logging, HTTP app and arq worker.

Sits above core and shared (app -> core -> shared); nothing imports app back —
enforced by the import-linter layers contract.
"""
