// Runs the app's sign-in and inventory code against the real Python server.
// Opt-in: BACCHUS_SERVER_TESTS=1 flutter test test/server_contract_test.dart
// (needs python3 and the repository's server/ directory next to mobile/).
import 'dart:io';

import 'package:bacchus_mobile/src/api.dart';
import 'package:bacchus_mobile/src/inventory.dart';
import 'package:bacchus_mobile/src/session.dart';
import 'package:flutter_test/flutter_test.dart';

import 'fakes.dart';

const systemToken = 'contract-system-token-0123456789';

void main() {
  final enabled = Platform.environment['BACCHUS_SERVER_TESTS'] == '1';
  late Process server;
  late Directory data;
  late String base;

  setUpAll(() async {
    if (!enabled) return;
    data = await Directory.systemTemp.createTemp('bacchus-contract');
    final port = 18000 + DateTime.now().millisecond;
    base = 'http://127.0.0.1:$port';
    server = await Process.start('python3', ['-m', 'server.app'], workingDirectory: Directory.current.parent.path, environment: {
      'PORT': '$port',
      'DATABASE_PATH': '${data.path}/db.sqlite3',
      'BACCHUS_USERS': '{"$systemToken":{"name":"運営","role":"system_admin"}}',
      'BACCHUS_PBKDF2_ITERATIONS': '1000',
    });
    for (var i = 0; i < 50; i++) {
      try {
        await ApiClient(Uri.parse(base)).get('/api/health');
        return;
      } on ApiException {
        await Future<void>.delayed(const Duration(milliseconds: 100));
      }
    }
    fail('server did not start');
  });

  tearDownAll(() async {
    if (!enabled) return;
    server.kill();
    await data.delete(recursive: true);
  });

  test('password sign-in, biometric device, inventory and token reuse', () async {
    final system = ApiClient(Uri.parse(base), token: systemToken);
    final created = await system.post('/api/system', {'action': 'createTenant', 'name': 'A店', 'adminName': '花子', 'adminEmail': 'hanako@example.com'}) as Map<String, dynamic>;
    final temporary = created['temporaryPassword'] as String;

    final vault = MemoryVault();
    final auth = AuthController(vault: vault, biometrics: FixedBiometrics(true));
    // The first password change belongs to the web app.
    await expectLater(auth.login(base, 'hanako@example.com', temporary), throwsA(isA<ApiException>().having((e) => e.message, 'message', contains('Web版'))));
    final web = ApiClient(Uri.parse(base));
    web.token = (await web.post('/api/auth/login', {'email': 'hanako@example.com', 'password': temporary}) as Map)['session'] as String;
    await web.post('/api/auth/password', {'currentPassword': temporary, 'newPassword': 'hanako-strong-pw'});
    await web.post('/api/commands', command('addFridge', {'id': 'f', 'name': '本店'}));

    await auth.login(base, 'hanako@example.com', 'hanako-strong-pw');
    expect(auth.session!.tenantName, 'A店');
    await auth.registerDevice('花子のiPhone', 'ios');
    final firstToken = vault.storedToken;
    await auth.logout();

    final phone = AuthController(vault: vault, biometrics: FixedBiometrics(true));
    await phone.load();
    expect(phone.deviceRegistered, isTrue);
    await phone.unlock();
    expect(phone.signedIn, isTrue);
    expect(vault.storedToken, isNot(firstToken));

    final received = await phone.api!.post('/api/commands', command('receive', {'name': '獺祭', 'volume': 720, 'count': 1, 'ids': [newId()], 'fridgeId': 'f', 'photo': ''})) as Map<String, dynamic>;
    final bottle = InventoryState.fromJson(received).bottles.single;
    final consumed = InventoryState.fromJson(await phone.api!.post('/api/commands', command('consume', {'bottleId': bottle.id, 'version': bottle.version})) as Map<String, dynamic>);
    expect(consumed.bottles.single.remainingLabel, '3合');
    expect(consumed.events.map((e) => e.actor).toSet(), {'花子'});

    // Replaying the rotated-out token unregisters the device on the server.
    final replay = ApiClient(Uri.parse(base));
    await expectLater(replay.post('/api/auth/device-login', {'deviceId': vault.storedDeviceId, 'deviceToken': firstToken}), throwsA(isA<ApiException>().having((e) => e.status, 'status', 401)));
    phone.expired();
    await expectLater(phone.unlock(), throwsA(isA<ApiException>()));
    expect(phone.deviceRegistered, isFalse);
  }, skip: enabled ? false : 'set BACCHUS_SERVER_TESTS=1 to run against the Python server');
}
