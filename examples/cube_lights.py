#!/usr/bin/env python
"""Connect a discovered cube and cycle its lights (requires a nearby cube)."""
import threading
import time

import pycozmo


with pycozmo.connect() as cli:
    print("Waiting for cube...")
    deadline = time.monotonic() + 30
    factory_id = None
    cube_types = {pycozmo.protocol_encoder.ObjectType.Block_LIGHTCUBE1,
                  pycozmo.protocol_encoder.ObjectType.Block_LIGHTCUBE2,
                  pycozmo.protocol_encoder.ObjectType.Block_LIGHTCUBE3}
    while time.monotonic() < deadline:
        factory_id = next((ident for ident, obj in list(cli.available_objects.items())
                           if obj.object_type in cube_types), None)
        if factory_id is not None:
            break
        time.sleep(0.1)
    if factory_id is None:
        raise RuntimeError("No cube detected; wake it by moving or tapping it.")

    ready = threading.Event()
    result = {}

    def on_connection(_client, packet):
        if packet.factory_id == factory_id and packet.connected:
            result['object_id'] = packet.object_id
            ready.set()

    # Register before requesting connection so a quick response cannot be lost.
    handler = cli.add_handler(pycozmo.protocol_encoder.ObjectConnectionState, on_connection)
    try:
        cli.set_object_connection(factory_id)
        if not ready.wait(10):
            raise RuntimeError("Cube connection timed out.")
        cube_id = result['object_id']
        print("Cube connected as object ID {}.".format(cube_id))
        for light in (pycozmo.lights.red_light, pycozmo.lights.green_light,
                      pycozmo.lights.blue_light, pycozmo.lights.off_light):
            cli.conn.send(pycozmo.protocol_encoder.CubeId(object_id=cube_id))
            cli.conn.send(pycozmo.protocol_encoder.CubeLights(states=(light,) * 4))
            time.sleep(2)
    finally:
        cli.del_handler(pycozmo.protocol_encoder.ObjectConnectionState, handler)
