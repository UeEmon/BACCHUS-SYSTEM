import 'package:bacchus_mobile/src/api.dart';
import 'package:bacchus_mobile/src/session.dart';
import 'package:flutter_test/flutter_test.dart';

import 'fakes.dart';

const server = 'https://sake.example.com';

AuthController controller(FakeServer fake, {MemoryVault? vault, SsoBrowser? browser}) => AuthController(
      vault: vault ?? MemoryVault(),
      biometrics: FixedBiometrics(true),
      client: fake.client,
      browser: browser ?? (url, scheme) async => throw StateError('no browser'),
    );

void main() {
  test('password sign-in without MFA', () async {
    final fake = FakeServer({
      '/api/auth/login': (body, _) => body['password'] == 'pw-123456789' ? {'session': 's1'} : const ServerError(401, '違います'),
      '/api/session': (_, token) => token == 's1' ? sessionJson() : const ServerError(401, 'ログインしてください'),
    });
    final auth = controller(fake);
    await auth.load();
    await auth.login(server, 'taro@example.com', 'pw-123456789');
    expect(auth.signedIn, isTrue);
    expect(auth.session!.tenantName, 'A店');
    expect(auth.shouldOfferBiometrics, isTrue);
    await expectLater(auth.login(server, 'taro@example.com', 'wrong'), throwsA(isA<ApiException>().having((e) => e.message, 'message', '違います')));
  });

  test('MFA challenge comes before the session', () async {
    final fake = FakeServer({
      '/api/auth/login': (_, _) => {'mfaRequired': true, 'challenge': 'c1'},
      '/api/auth/mfa/verify': (body, _) => body['challenge'] == 'c1' && body['code'] == '123456' ? {'session': 's2'} : const ServerError(401, '確認コードが違います'),
      '/api/session': (_, token) => sessionJson(),
    });
    final auth = controller(fake);
    await auth.login(server, 'taro@example.com', 'pw-123456789');
    expect((auth.awaitingMfa, auth.signedIn), (true, false));
    await expectLater(auth.verifyMfa('000000'), throwsA(isA<ApiException>()));
    await auth.verifyMfa(' 123456 ');
    expect((auth.awaitingMfa, auth.signedIn), (false, true));
  });

  test('first-time steps and system administrators are sent to the web app', () async {
    for (final info in [sessionJson(mustChange: true), sessionJson(mfaSetup: true), sessionJson(role: 'system_admin', tenant: false)]) {
      final fake = FakeServer({
        '/api/auth/login': (_, _) => {'session': 's'},
        '/api/session': (_, _) => info,
        '/api/auth/logout': (_, _) => <String, dynamic>{},
      });
      final auth = controller(fake);
      await expectLater(auth.login(server, 'a@example.com', 'pw-123456789'), throwsA(isA<ApiException>().having((e) => e.message, 'message', contains('Web版'))));
      expect(auth.signedIn, isFalse);
      expect(fake.calls, contains('/api/auth/logout'));
    }
  });

  test('device registration, biometric unlock with rotation, and revocation', () async {
    var current = 'device-token-1';
    final fake = FakeServer({
      '/api/auth/login': (_, _) => {'session': 's'},
      '/api/session': (_, _) => sessionJson(),
      '/api/auth/devices': (body, _) => {'deviceId': 'd1', 'deviceToken': current},
      '/api/auth/device-login': (body, _) {
        if (body['deviceToken'] != current) return const ServerError(401, '端末の登録を解除しました');
        current = 'device-token-${int.parse(current.split('-').last) + 1}';
        return {'session': 's-device', 'deviceToken': current};
      },
      '/api/auth/logout': (_, _) => <String, dynamic>{},
    });
    final vault = MemoryVault();
    final auth = controller(fake, vault: vault);
    await auth.login(server, 'taro@example.com', 'pw-123456789');
    await auth.registerDevice('太郎のiPhone', 'ios');
    expect((vault.storedDeviceId, vault.storedToken, auth.deviceRegistered), ('d1', 'device-token-1', true));
    await auth.logout();

    final next = controller(fake, vault: vault);
    await next.load();
    expect(next.deviceRegistered, isTrue);
    await next.unlock();
    expect(next.signedIn, isTrue);
    expect(vault.storedToken, 'device-token-2');
    expect(vault.tokenReads, 1);

    // The server refuses (for example after the device was revoked): the phone forgets it.
    current = 'revoked';
    next.expired();
    await expectLater(next.unlock(), throwsA(isA<ApiException>()));
    expect((next.deviceRegistered, vault.storedToken), (false, null));
  });

  test('SSO returns through the app scheme with a one-time code', () async {
    final fake = FakeServer({
      '/api/auth/sso/start': (body, _) => body['client'] == 'app' ? {'url': 'https://idp.example/authorize?state=x'} : const ServerError(400, 'web only'),
      '/api/auth/exchange': (body, _) => body['code'] == 'one-time' ? {'session': 's-sso'} : const ServerError(401, '期限切れ'),
      '/api/session': (_, _) => sessionJson(),
    });
    String? scheme;
    final auth = controller(fake, browser: (url, s) async {
      scheme = s;
      return 'bacchus://sso?code=one-time';
    });
    await auth.loginWithSso(server, 'taro@example.com');
    expect((auth.signedIn, scheme), (true, 'bacchus'));

    final failing = controller(fake, browser: (url, s) async => 'bacchus://sso?error=${Uri.encodeComponent('SSOのメールアドレスが登録と一致しません')}');
    await expectLater(failing.loginWithSso(server, 'taro@example.com'), throwsA(isA<ApiException>().having((e) => e.message, 'message', contains('一致しません'))));
  });
}
