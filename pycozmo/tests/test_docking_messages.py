import struct

from pycozmo import protocol_encoder as pe


def test_dock_with_object_layout():
    pkt = pe.DockWithObject(action=pe.DockAction.DA_PICKUP_LOW, docking_method=pe.DockingMethod.HYBRID_DOCKING,
                            num_retries=2, do_lift_load_check=True)
    data = pkt.to_bytes()
    assert len(data) == 21
    assert pe.DockWithObject().id == 0x42
    reserved, speed, accel, decel, action, check, retries, method, back_up = struct.unpack('<IfffBBBBB', data)
    assert (reserved, speed, accel, decel) == (0, 60.0, 200.0, 500.0)
    assert (action, check, retries, method, back_up) == (0, 1, 2, 2, 0)


def test_docking_error_signal_layout():
    pkt = pe.DockingErrorSignal(timestamp=1234, x_dist_err_mm=120.5, y_hor_err_mm=-3.0, z_height_mm=22.0,
                                angle_err_rad=.1, did_tracking_succeed=True)
    data = pkt.to_bytes()
    assert len(data) == 22 and pe.DockingErrorSignal().id == 0x48
    angle = struct.unpack('<f', struct.pack('<f', .1))[0]
    assert struct.unpack('<Iffff??', data) == (1234, 120.5, -3.0, 22.0, angle, False, True)


def test_empty_docking_commands():
    assert pe.AbortDocking().to_bytes() == b'' and pe.AbortDocking().id == 0x43
    assert pe.CheckLiftLoad().to_bytes() == b'' and pe.CheckLiftLoad().id == 0x4f


def test_docking_events_decode():
    result = pe.PickAndPlaceResult.from_bytes(struct.pack('<I?BB', 99, True, 2, 0))
    assert result.timestamp == 99 and result.did_succeed and result.result == pe.DockingResult.DOCK_SUCCESS
    status = pe.DockingStatus.from_bytes(struct.pack('<IB', 7, 3))
    assert (status.timestamp, status.status) == (7, 3)
    assert pe.LiftLoad.from_bytes(b'\x01').has_load
    assert pe.MovingLiftPostDock.from_bytes(b'\x01').value == 1
    assert [pe.PickAndPlaceResult().id, pe.MovingLiftPostDock().id, pe.DockingStatus().id, pe.LiftLoad().id] == [
        0xb8, 0xc5, 0xd3, 0xda]


def test_set_carry_state():
    pkt = pe.SetCarryState(state=pe.CarryState.CARRY_NONE)
    assert pkt.to_bytes() == b'\x00' and pkt.id == 0x49
    assert pe.SetCarryState(state=pe.CarryState.CARRY_1_BLOCK).to_bytes() == b'\x01'
