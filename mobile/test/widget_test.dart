import 'package:bacchus_mobile/main.dart';
import 'package:bacchus_mobile/src/session.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'fakes.dart';

void main() {
  testWidgets('login screen validates input and switches to the MFA step', (tester) async {
    final fake = FakeServer({
      '/api/auth/login': (_, _) => {'mfaRequired': true, 'challenge': 'c'},
    });
    final auth = AuthController(vault: MemoryVault(), biometrics: FixedBiometrics(false), client: fake.client);
    await auth.load();
    await tester.pumpWidget(BacchusApp(auth: auth));
    await tester.pumpAndSettle();

    expect(find.text('BACCHUS'), findsOneWidget);
    await tester.tap(find.text('ログイン'));
    await tester.pump();
    expect(find.text('サーバーのURLを入力してください'), findsOneWidget);
    expect(find.text('メールアドレスを入力してください'), findsOneWidget);

    await tester.enterText(find.widgetWithText(TextFormField, 'サーバー'), 'sake.example.com');
    await tester.enterText(find.widgetWithText(TextFormField, 'メールアドレス'), 'taro@example.com');
    await tester.enterText(find.widgetWithText(TextFormField, 'パスワード'), 'pw-123456789');
    await tester.tap(find.text('ログイン'));
    await tester.pumpAndSettle();
    expect(find.text('確認コード'), findsOneWidget);
    await tester.tap(find.text('最初からやり直す'));
    await tester.pumpAndSettle();
    expect(find.text('SSOでログイン'), findsOneWidget);
  });

  testWidgets('a registered phone opens on the biometric unlock screen', (tester) async {
    final vault = MemoryVault()
      ..storedServer = 'https://sake.example.com'
      ..storedDeviceId = 'd1'
      ..storedToken = 't';
    final fake = FakeServer({
      '/api/auth/device-login': (_, _) => const ServerError(401, '端末ログインに失敗しました'),
    });
    final auth = AuthController(vault: vault, biometrics: FixedBiometrics(true), client: fake.client);
    await auth.load();
    await tester.pumpWidget(BacchusApp(auth: auth));
    await tester.pumpAndSettle();
    // The automatic attempt was refused, so the phone forgot its registration and offers the login form.
    expect(find.text('ログイン'), findsOneWidget);
    expect(vault.storedDeviceId, isNull);
    expect(fake.calls, ['/api/auth/device-login']);
  });
}
