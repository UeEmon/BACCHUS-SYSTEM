import 'package:flutter/material.dart';

import 'src/screens/home_screen.dart';
import 'src/screens/login_screen.dart';
import 'src/screens/unlock_screen.dart';
import 'src/session.dart';
import 'src/vault.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  final auth = AuthController(vault: SecureCredentialVault(), biometrics: DeviceBiometricSupport())..load();
  runApp(BacchusApp(auth: auth));
}

class BacchusApp extends StatelessWidget {
  const BacchusApp({super.key, required this.auth});

  final AuthController auth;

  static const green = Color(0xFF173D37);

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'BACCHUS',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(colorScheme: ColorScheme.fromSeed(seedColor: green), scaffoldBackgroundColor: const Color(0xFFF5F5EF)),
      home: ListenableBuilder(
        listenable: auth,
        builder: (context, _) {
          if (!auth.ready) return const Scaffold(body: Center(child: CircularProgressIndicator()));
          if (auth.signedIn) return HomeScreen(auth: auth);
          if (auth.deviceRegistered && !auth.awaitingMfa) return UnlockScreen(auth: auth);
          return LoginScreen(auth: auth);
        },
      ),
    );
  }
}
