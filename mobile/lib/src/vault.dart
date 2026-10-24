import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:local_auth/local_auth.dart';

/// What the app remembers to sign in again with a fingerprint or face.
class DeviceCredential {
  const DeviceCredential({required this.deviceId, required this.token});

  final String deviceId;
  final String token;
}

/// Storage for the server address and the device credential.
///
/// The device token is the only secret. It lives in an item the operating
/// system releases only after a biometric check: the iOS Keychain with the
/// currently enrolled biometrics, and an Android Keystore key that requires
/// strong biometric authentication. Reading it shows the system prompt.
abstract class CredentialVault {
  Future<String?> server();
  Future<void> saveServer(String server);
  Future<String?> deviceId();
  Future<String?> readToken();
  Future<void> save(DeviceCredential credential);
  Future<void> clear();
}

class SecureCredentialVault implements CredentialVault {
  SecureCredentialVault({FlutterSecureStorage? plain, FlutterSecureStorage? biometric})
      : _plain = plain ?? const FlutterSecureStorage(),
        _biometric = biometric ??
            const FlutterSecureStorage(
              iOptions: IOSOptions(
                accountName: 'bacchus_device_token',
                accessibility: KeychainAccessibility.passcode,
                accessControlFlags: [AccessControlFlag.biometryCurrentSet],
              ),
              aOptions: AndroidOptions.biometric(
                enforceBiometrics: true,
                biometricType: AndroidBiometricType.strongBiometricOnly,
                biometricPromptTitle: 'BACCHUSにログイン',
                biometricPromptSubtitle: '生体認証で本人確認します',
              ),
            );

  final FlutterSecureStorage _plain;
  final FlutterSecureStorage _biometric;

  @override
  Future<String?> server() => _plain.read(key: 'server');

  @override
  Future<void> saveServer(String server) => _plain.write(key: 'server', value: server);

  @override
  Future<String?> deviceId() => _plain.read(key: 'device_id');

  @override
  Future<String?> readToken() => _biometric.read(key: 'device_token');

  @override
  Future<void> save(DeviceCredential credential) async {
    await _biometric.write(key: 'device_token', value: credential.token);
    await _plain.write(key: 'device_id', value: credential.deviceId);
  }

  @override
  Future<void> clear() async {
    await _plain.delete(key: 'device_id');
    await _biometric.delete(key: 'device_token');
  }
}

/// Whether this phone can protect the device token with biometrics.
abstract class BiometricSupport {
  Future<bool> available();
}

class DeviceBiometricSupport implements BiometricSupport {
  DeviceBiometricSupport([LocalAuthentication? auth]) : _auth = auth ?? LocalAuthentication();

  final LocalAuthentication _auth;

  @override
  Future<bool> available() async {
    try {
      return await _auth.isDeviceSupported() && (await _auth.getAvailableBiometrics()).isNotEmpty;
    } on Exception {
      return false;
    }
  }
}
