#!/usr/bin/env bash
# Prepares App Store signing on a GitHub Actions macOS runner (run from mobile/).
# Inputs (GitHub secrets): IOS_DIST_CERT_P12_BASE64, IOS_DIST_CERT_PASSWORD, IOS_PROVISION_PROFILE_BASE64.
# Installs the certificate in a temporary keychain, installs the provisioning profile, and writes
# ios/Flutter/Signing.xcconfig and $RUNNER_TEMP/ExportOptions.plist for `flutter build ipa`.
set -euo pipefail

BUNDLE_ID="jp.bacchus.app"
work="${RUNNER_TEMP:?}"
cert="$work/distribution.p12"
profile="$work/app.mobileprovision"
keychain="$work/signing.keychain-db"

printf '%s' "$IOS_DIST_CERT_P12_BASE64" | base64 --decode > "$cert"
printf '%s' "$IOS_PROVISION_PROFILE_BASE64" | base64 --decode > "$profile"

# Read the profile and stop early with a clear message if it does not fit this app.
security cms -D -i "$profile" > "$work/profile.plist"
profile_name=$(plutil -extract Name raw "$work/profile.plist")
profile_uuid=$(plutil -extract UUID raw "$work/profile.plist")
team_id=$(plutil -extract TeamIdentifier.0 raw "$work/profile.plist")
app_id=$(plutil -extract Entitlements.application-identifier raw "$work/profile.plist")
expires=$(plutil -extract ExpirationDate raw "$work/profile.plist")
if [[ "$app_id" != "$team_id.$BUNDLE_ID" ]]; then
  echo "::error::プロビジョニングプロファイルのApp ID（$app_id）が $team_id.$BUNDLE_ID と一致しません。$BUNDLE_ID 用のApp Storeプロファイルを登録してください"
  exit 1
fi
if plutil -extract ProvisionedDevices raw "$work/profile.plist" >/dev/null 2>&1; then
  echo "::error::登録されたプロファイルは開発用またはAd Hoc用です。App Store（配布）用のプロファイルを登録してください"
  exit 1
fi
echo "Profile: $profile_name (team $team_id, expires $expires)"

# A keychain that exists only for this job, with a random password.
keychain_password=$(openssl rand -base64 24)
security create-keychain -p "$keychain_password" "$keychain"
security set-keychain-settings -lut 21600 "$keychain"
security unlock-keychain -p "$keychain_password" "$keychain"
security import "$cert" -P "$IOS_DIST_CERT_PASSWORD" -A -t cert -f pkcs12 -k "$keychain"
security set-key-partition-list -S apple-tool:,apple: -k "$keychain_password" "$keychain" >/dev/null
security list-keychains -d user -s "$keychain" $(security list-keychains -d user | tr -d '"')
if ! security find-identity -v -p codesigning "$keychain" | grep -q "Apple Distribution"; then
  echo "::error::証明書にApple Distributionの署名IDがありません。配布用（Apple Distribution）証明書を秘密鍵ごと.p12で書き出してください"
  exit 1
fi
rm -f "$cert"

# Xcode 16 and later read profiles from UserData; older versions from MobileDevice.
for dir in "$HOME/Library/Developer/Xcode/UserData/Provisioning Profiles" "$HOME/Library/MobileDevice/Provisioning Profiles"; do
  mkdir -p "$dir"
  cp "$profile" "$dir/$profile_uuid.mobileprovision"
done

cat > ios/Flutter/Signing.xcconfig <<EOF
CODE_SIGN_STYLE = Manual
DEVELOPMENT_TEAM = $team_id
PROVISIONING_PROFILE_SPECIFIER = $profile_name
CODE_SIGN_IDENTITY = Apple Distribution
CODE_SIGN_IDENTITY[sdk=iphoneos*] = Apple Distribution
EOF

cat > "$work/ExportOptions.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>method</key><string>app-store-connect</string>
  <key>destination</key><string>export</string>
  <key>teamID</key><string>$team_id</string>
  <key>signingStyle</key><string>manual</string>
  <key>signingCertificate</key><string>Apple Distribution</string>
  <key>provisioningProfiles</key>
  <dict><key>$BUNDLE_ID</key><string>$profile_name</string></dict>
  <key>uploadSymbols</key><true/>
</dict>
</plist>
EOF
echo "KEYCHAIN_PATH=$keychain" >> "$GITHUB_ENV"
