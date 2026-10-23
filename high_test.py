#!/usr/bin/env python
# Copyright 2015 Google Inc. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import absolute_import

import logging
import socket
import sys
import unittest
from unittest import mock

from adb import common
from adb import usb_exceptions
from adb.contrib import adb_commands_safe
from adb.contrib import high


class MockDevice(object):
  def __init__(self, cmds):
    super(MockDevice, self).__init__()
    self._cmds = cmds[:]
    self.port_path = (0, 0)

  def IsShellOk(self, cmd):  # pylint: disable=unused-argument
    return True

  def Shell(self, cmd, timeout_ms=None):
    data = self._cmds.pop(0)
    assert data[0] == cmd, (data, cmd)
    return data[1], 0


RAW_IMEI = """Result: Parcel(
  0x00000000: 00000000 0000000f 00350033 00320035 '........3.5.5.2.'
  0x00000010: 00360033 00350030 00360038 00350038 '3.6.0.5.8.6.8.5.'
  0x00000020: 00390038 00000034                   '8.9.4...        ')
"""

class TestAndroid(unittest.TestCase):
  def test_GetIMEI(self):
    device = MockDevice(
        [
          ('dumpsys iphonesubinfo', ''),
          ('service call iphonesubinfo 1', RAW_IMEI),
        ])
    cache = high.DeviceCache(None, None, None, None, None)
    self.assertEqual(
        u'355236058685894', high.HighDevice(device, cache).GetIMEI())


class TestGetTemperatures(unittest.TestCase):
  def setUp(self):
    device = MockDevice([])
    cache = high.DeviceCache({'ro.build.version.sdk': 30}, {}, {}, {}, {})
    self.high_device = high.HighDevice(device, cache)

  def test_temperature_fallback_unused_if_default_path_works(self):
    """Ensures the fallback is only used if necessary."""
    with mock.patch.object(self.high_device,
                           '_GetTemperaturesFromSysFiles',
                           return_value={'sensor': 1.0}):
      with mock.patch.object(self.high_device,
                             '_GetTemperaturesFromThermalService',
                             side_effect=RuntimeError):
        temperature_data = self.high_device.GetTemperatures()
    self.assertEqual(temperature_data, {'sensor': 1.0})

  def test_temperature_fallback_sdk_level_below_minimum(self):
    """Ensures that the fallback exits early below Android 10."""
    self.high_device.cache.build_props['ro.build.version.sdk'] = 28
    with mock.patch.object(
        self.high_device, 'Dumpsys', side_effect=RuntimeError):
      temperature_data = self.high_device._GetTemperaturesFromThermalService()
    self.assertEqual(temperature_data, {})

  def test_temperature_fallback_dumpsys_failure(self):
    """Ensures dumpsys failures are handled gracefully."""
    with self.assertLogs(level='ERROR') as log_manager:
      with mock.patch.object(self.high_device, 'Dumpsys', return_value=None):
        temperature_data = self.high_device._GetTemperaturesFromThermalService()
      self.assertIn(
          'ERROR:adb.high:Failed to run "dumpsys thermalservice" during '
          'fallback temperature collection', log_manager.output)
    self.assertEqual(temperature_data, {})

  def test_temperature_fallback_happy_path(self):
    """Ensures the temperature fallback happy path works as expected."""
    with mock.patch.object(self.high_device, 'Dumpsys', return_value='foo'):
      with mock.patch.object(high,
                             '_ParseThermalServiceOutput',
                             return_value={'sensor': 1.0}) as parse_mock:
        temperature_data = self.high_device._GetTemperaturesFromThermalService()
        parse_mock.assert_called_with('foo')
    self.assertEqual(temperature_data, {'sensor': 1.0})

  def test_temperature_parsing_happy_path(self):
    """Ensures the temperature parsing happy path works as expected."""
    # Real "dumpsys thermalservice" output from a Samsung S24.
    # pylint: disable=line-too-long
    dumpsys_output = """
IsStatusOverride: false
ThermalEventListeners:
    callbacks: 1
    killed: false
    broadcasts count: -1
ThermalStatusListeners:
    callbacks: 4
    killed: false
    broadcasts count: -1
Thermal Status: 0
Cached temperatures:
    Temperature{mValue=0.0, mType=2, mName=SUBBAT, mStatus=0}
    Temperature{mValue=26.7, mType=0, mName=AP, mStatus=0}
    Temperature{mValue=21.7, mType=12, mName=CP, mStatus=0}
    Temperature{mValue=20.8, mType=5, mName=PA, mStatus=0}
    Temperature{mValue=18.8, mType=2, mName=BAT, mStatus=0}
    Temperature{mValue=19.7, mType=4, mName=USB, mStatus=0}
    Temperature{mValue=23.3, mType=3, mName=SKIN, mStatus=0}
HAL Ready: true
HAL connection:
    ThermalHAL AIDL 1  connected: yes
Current temperatures from HAL:
    Temperature{mValue=19.1, mType=0, mName=AP, mStatus=0}
    Temperature{mValue=18.6, mType=2, mName=BAT, mStatus=0}
    Temperature{mValue=19.0, mType=12, mName=CP, mStatus=0}
    Temperature{mValue=19.1, mType=5, mName=PA, mStatus=0}
    Temperature{mValue=21.7, mType=3, mName=SKIN, mStatus=0}
    Temperature{mValue=0.0, mType=2, mName=SUBBAT, mStatus=0}
    Temperature{mValue=19.4, mType=4, mName=USB, mStatus=0}
Current cooling devices from HAL:
Temperature static thresholds from HAL:
    TemperatureThreshold{mType=2, mName=BAT, mHotThrottlingThresholds=[NaN, NaN, NaN, NaN, NaN, 55.0, 85.0], mColdThrottlingThresholds=[NaN, NaN, NaN, NaN, NaN, NaN, NaN]}
    TemperatureThreshold{mType=3, mName=SKIN, mHotThrottlingThresholds=[36.0, 38.0, 40.0, 42.0, 45.0, NaN, NaN], mColdThrottlingThresholds=[NaN, NaN, NaN, NaN, NaN, NaN, NaN]}"""
    # pylint: enable=line-too-long
    temperature_data = high._ParseThermalServiceOutput(dumpsys_output)
    self.assertEqual(
        temperature_data, {
            'AP': 19.1,
            'BAT': 18.6,
            'CP': 19.0,
            'PA': 19.1,
            'SKIN': 21.7,
            'USB': 19.4,
        })

  def test_temperature_parsing_malformed_line(self):
    """Tests behavior when a malformed temperature line is present."""
    dumpsys_output = """
Current temperatures from HAL:
    Temperature{mValue=19.1, mType=0, mName=AP, mStatus=0}
    Temperature{malformed
    Temperature{mValue=19.4, mType=4, mName=USB, mStatus=0}"""
    with self.assertLogs(level='ERROR') as log_manager:
      temperature_data = high._ParseThermalServiceOutput(dumpsys_output)
      self.assertIn(
          'ERROR:adb.high:Unable to find expected temperature data in line '
          "'Temperature{malformed'", log_manager.output)
    self.assertEqual(temperature_data, {
        'AP': 19.1,
        'USB': 19.4,
    })

  def test_temperature_parsing_invalid_float(self):
    """Tests behavior when a non-float value is found."""
    dumpsys_output = """
Current temperatures from HAL:
    Temperature{mValue=19.1, mType=0, mName=AP, mStatus=0}
    Temperature{mValue=VeryHot, mType=3, mName=SKIN, mStatus=0}
    Temperature{mValue=19.4, mType=4, mName=USB, mStatus=0}"""
    with self.assertLogs(level='ERROR') as log_manager:
      temperature_data = high._ParseThermalServiceOutput(dumpsys_output)
      self.assertIn("ERROR:adb.high:Unable to parse float from 'VeryHot'",
                    log_manager.output)
    self.assertEqual(temperature_data, {
        'AP': 19.1,
        'USB': 19.4,
    })

  def test_temperature_parsing_no_data(self):
    """Tests behavior when no temperature data is found."""
    dumpsys_output = """
Cached temperatures:
    Temperature{mValue=0.0, mType=2, mName=SUBBAT, mStatus=0}"""
    with self.assertLogs(level='WARNING') as log_manager:
      previous_level = high._LOG.level
      high._LOG.setLevel(logging.WARNING)
      try:
        temperature_data = high._ParseThermalServiceOutput(dumpsys_output)
      finally:
        high._LOG.setLevel(previous_level)
      self.assertIn(
          'WARNING:adb.high:Did not find any data using fallback temperature '
          'path',
          log_manager.output)
    self.assertEqual(temperature_data, {})


class FakeSocket(object):
  """Minimal stand-in for a connected socket.socket."""

  def __init__(self, recv_error=None, send_error=None):
    self.recv_error = recv_error
    self.send_error = send_error
    self.closed = False

  def settimeout(self, _timeout):
    pass

  def sendall(self, _data):
    if self.send_error:
      raise self.send_error

  def recv(self, _length):
    if self.recv_error:
      raise self.recv_error
    return b''

  def close(self):
    self.closed = True


class TestTcpHandleErrors(unittest.TestCase):
  def setUp(self):
    super(TestTcpHandleErrors, self).setUp()
    patcher = mock.patch.object(common.socket, 'create_connection')
    self.create_connection = patcher.start()
    self.addCleanup(patcher.stop)

  def _open_handle(self, sock):
    self.create_connection.return_value = sock
    handle = common.TcpHandle('test_host:5555')
    handle.Open()
    return handle

  def test_reset_does_not_raise_when_unreachable(self):
    self.create_connection.side_effect = ConnectionRefusedError(
        111, 'Connection refused')
    handle = common.TcpHandle('test_host:5555')
    handle.Reset()
    self.assertFalse(handle.is_open)
    self.assertEqual(handle.serial_number, 'test_host:5555')

  def test_reset_new_endpoint_updates_serial_when_unreachable(self):
    self.create_connection.side_effect = socket.timeout('timed out')
    handle = common.TcpHandle('test_host:5555')
    handle.Reset(new_endpoint='other_host:5556')
    self.assertFalse(handle.is_open)
    self.assertEqual(handle.serial_number, 'other_host:5556')

  def test_reset_reopens(self):
    old, new = FakeSocket(), FakeSocket()
    self.create_connection.side_effect = [old, new]
    handle = common.TcpHandle('test_host:5555')
    handle.Open()
    handle.Reset()
    self.assertTrue(handle.is_open)
    self.assertTrue(old.closed)
    self.assertFalse(new.closed)

  def test_bulk_read_wraps_connection_reset(self):
    error = ConnectionResetError(104, 'Connection reset by peer')
    handle = self._open_handle(FakeSocket(recv_error=error))
    with self.assertRaises(usb_exceptions.ReadFailedError) as cm:
      handle.BulkRead(24)
    self.assertIs(cm.exception.usb_error, error)
    self.assertIn('test_host:5555', str(cm.exception))
    self.assertNotIn('timeout', str(cm.exception))

  def test_bulk_read_timeout_message(self):
    handle = self._open_handle(
        FakeSocket(recv_error=socket.timeout('timed out')))
    with self.assertRaises(usb_exceptions.ReadFailedError) as cm:
      handle.BulkRead(24)
    self.assertIn('(timeout 1000ms)', str(cm.exception))

  def test_bulk_read_connection_closed(self):
    handle = self._open_handle(FakeSocket())
    with self.assertRaises(usb_exceptions.ReadFailedError) as cm:
      handle.BulkRead(24)
    self.assertIsNone(cm.exception.usb_error)
    self.assertIn('Connection closed by remote end', str(cm.exception))

  def test_bulk_write_wraps_broken_pipe(self):
    error = BrokenPipeError(32, 'Broken pipe')
    handle = self._open_handle(FakeSocket(send_error=error))
    with self.assertRaises(usb_exceptions.ReadFailedError) as cm:
      handle.BulkWrite(b'data')
    self.assertIs(cm.exception.usb_error, error)
    self.assertNotIn('timeout', str(cm.exception))

  def test_bulk_write_timeout_message(self):
    handle = self._open_handle(
        FakeSocket(send_error=socket.timeout('timed out')))
    with self.assertRaises(usb_exceptions.ReadFailedError) as cm:
      handle.BulkWrite(b'data')
    self.assertIn('(timeout 1000ms)', str(cm.exception))


class TestTcpReconnectAndReset(unittest.TestCase):
  def setUp(self):
    super(TestTcpReconnectAndReset, self).setUp()
    patcher = mock.patch.object(common.socket, 'create_connection')
    self.create_connection = patcher.start()
    self.addCleanup(patcher.stop)

  def _new(self, **kwargs):
    return adb_commands_safe.AdbCommandsSafe(
        handle=common.TcpHandle('test_host:5555'), banner=b'test', rsa_keys=[],
        on_error=None, lost_timeout_ms=100, **kwargs)

  def test_tcp_reset_and_reconnect_handle_socket_errors(self):
    self.create_connection.side_effect = ConnectionRefusedError(
        111, 'Connection refused')
    dev = adb_commands_safe.AdbCommandsSafe.Connect(
        common.TcpHandle('test_host:5555'),
        banner=b'test',
        rsa_keys=[],
        on_error=None,
        lost_timeout_ms=100)
    self.assertFalse(dev.is_valid)
    self.assertFalse(dev.has_handle)
    self.assertEqual(dev.serial, 'test_host:5555')
    self.assertEqual(self.create_connection.call_count, 1)

    # ReconnectViaSerial should use preserved TCP serial and not raise
    # TypeError or OSError when the TCP endpoint remains unreachable.
    dev.ReconnectViaSerial()
    self.assertFalse(dev.has_handle)
    self.assertEqual(self.create_connection.call_count, 2)

    # ResetHandle should not raise, and should close the handle.
    dev._handle = common.TcpHandle('test_host:5555')
    self.assertFalse(dev.ResetHandle())
    self.assertFalse(dev.has_handle)
    self.assertEqual(dev.serial, 'test_host:5555')
    self.assertEqual(self.create_connection.call_count, 3)

  def test_connection_reset_during_handshake(self):
    # A connection reset during the CNXN handshake used to escape _Connect()
    # as a raw ConnectionResetError and crash bot hooks.
    self.create_connection.side_effect = lambda *_args, **_kwargs: FakeSocket(
        recv_error=ConnectionResetError(104, 'Connection reset by peer'))
    dev = adb_commands_safe.AdbCommandsSafe.Connect(
        common.TcpHandle('test_host:5555'),
        banner=b'test',
        rsa_keys=[],
        on_error=None,
        lost_timeout_ms=100)
    self.assertFalse(dev.is_valid)
    self.assertEqual(dev.failure, 'usb_failure')

  def test_reset_handle_new_endpoint_when_unreachable(self):
    dev = self._new()
    self.create_connection.side_effect = ConnectionRefusedError(
        111, 'Connection refused')
    self.assertFalse(dev.ResetHandle(new_endpoint='other_host:5556'))
    self.assertFalse(dev.has_handle)
    self.assertEqual(dev.serial, 'other_host:5556')
    # A later reconnect uses the new endpoint.
    dev.ReconnectViaSerial()
    self.assertEqual(
        self.create_connection.call_args[0][0], ('other_host', '5556'))

  def test_find_recreates_tcp_handle_with_default_timeout(self):
    dev = self._new(default_timeout_ms=10000)
    dev.Close()
    self.assertTrue(dev._Find(use_serial=False))
    self.assertEqual(dev._handle.serial_number, 'test_host:5555')
    self.assertEqual(dev._handle.Timeout(None), common.DEFAULT_TIMEOUT_MS)

  def test_serial_not_preset_for_local_handles(self):
    dev = adb_commands_safe.AdbCommandsSafe(
        handle=common.Handle(serial='usb_serial'), banner=b'test', rsa_keys=[],
        on_error=None)
    self.assertIsNone(dev.serial)


if __name__ == '__main__':
  if '-v' in sys.argv:
    unittest.TestCase.maxDiff = None  # pragma: no cover
  logging.basicConfig(
      level=logging.DEBUG if '-v' in sys.argv else logging.CRITICAL)
  unittest.main()
