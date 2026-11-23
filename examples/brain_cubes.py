#!/usr/bin/env python
"""
Run the brain with the cubes and the markers (needs the prepared resources, with the marker library).

Wake a cube (move or tap it): it connects, plays its "Connected" light animation and the robot reacts when the cube
moves or when it sees it. Press Ctrl+C to stop.
"""
import time

import pycozmo


with pycozmo.connect(enable_procedural_face=False) as cli:
    brain = pycozmo.brain.Brain(cli, markers=True)
    brain.start()
    try:
        cube_types = {pycozmo.protocol_encoder.ObjectType.Block_LIGHTCUBE1,
                      pycozmo.protocol_encoder.ObjectType.Block_LIGHTCUBE2,
                      pycozmo.protocol_encoder.ObjectType.Block_LIGHTCUBE3}
        connected = set()
        while True:
            for factory_id, obj in list(cli.available_objects.items()):
                if obj.object_type in cube_types and factory_id not in connected:
                    connected.add(factory_id)
                    cli.set_object_connection(factory_id)
            if brain.visible_markers:
                print(", ".join(m.name for m in brain.visible_markers))
            time.sleep(1.0)
    except KeyboardInterrupt:
        pass
    finally:
        brain.stop()
