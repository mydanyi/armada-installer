"""Decky entry point for the Armada installer plugin.

Blocking inspection and the systemd hand-off run in worker threads so a slow
call cannot stall Decky's asyncio loop.  The plugin holds no reference that a
destructor could use to cancel a running install; the fixed systemd unit keeps
running across UI closes and plugin reloads.
"""

import asyncio

from armada_installer.service import InstallerError, InstallerService


class Plugin:
    def __init__(self):
        self._service = InstallerService()

    async def _call(self, function, *args, **kwargs):
        try:
            data = await asyncio.to_thread(function, *args, **kwargs)
        except InstallerError as error:
            return {"ok": False, "error": error.code, "detail": error.detail}
        except Exception:
            # Never surface an unexpected traceback or internal detail to the UI.
            return {"ok": False, "error": "internal_error", "detail": ""}
        return {"ok": True, "data": data}

    async def get_inspection(self):
        return await self._call(self._service.inspect)

    async def prepare_install(self, android_gib=None):
        return await self._call(self._service.prepare, android_gib)

    async def start_install(self, token, confirmed=False):
        return await self._call(self._service.start, token, confirmed)

    async def get_status(self):
        return await self._call(self._service.status)

    async def power_off(self):
        return await self._call(self._service.poweroff)

    async def _main(self):
        return None

    async def _unload(self):
        return None
