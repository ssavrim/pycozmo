#!/usr/bin/env python
"""Connect a cube and play animations of its lights (requires a nearby cube and the prepared resources)."""
import threading
import time

import pycozmo
from pycozmo import cube_lights


with pycozmo.connect() as cli:
    root = str(pycozmo.util.get_cozmo_asset_dir())
    animations = cube_lights.load_cube_light_animations(root)

    print("Waiting for cube...")
    cube_types = {pycozmo.protocol_encoder.ObjectType.Block_LIGHTCUBE1,
                  pycozmo.protocol_encoder.ObjectType.Block_LIGHTCUBE2,
                  pycozmo.protocol_encoder.ObjectType.Block_LIGHTCUBE3}
    deadline = time.monotonic() + 30
    factory_id = None
    while factory_id is None and time.monotonic() < deadline:
        factory_id = next((i for i, o in list(cli.available_objects.items()) if o.object_type in cube_types), None)
        time.sleep(0.1)
    if factory_id is None:
        raise RuntimeError("No cube detected; wake it by moving or tapping it.")

    ready = threading.Event()
    result: dict[str, int] = {}

    def on_connection(_client, packet):
        if packet.factory_id == factory_id and packet.connected:
            result['object_id'] = packet.object_id
            ready.set()

    handler = cli.add_handler(pycozmo.protocol_encoder.ObjectConnectionState, on_connection)
    try:
        cli.set_object_connection(factory_id)
        if not ready.wait(10):
            raise RuntimeError("Cube connection timed out.")
        player = cube_lights.CubeLightPlayer.for_cube(cli, result['object_id'])
        for name in ("flash", "speedTapWin", "dancing_01", "pyramidFlourish"):
            print("Playing {}".format(name))
            finished = threading.Event()
            player.play(animations[name], on_finished=finished.set)
            while not finished.is_set():
                player.update()
                time.sleep(0.02)
            time.sleep(1)
        cli.set_cube_lights(result['object_id'], (pycozmo.lights.off_light,) * 4)
    finally:
        cli.del_handler(pycozmo.protocol_encoder.ObjectConnectionState, handler)
