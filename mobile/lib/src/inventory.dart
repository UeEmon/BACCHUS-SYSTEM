import 'dart:convert';
import 'dart:math';
import 'dart:typed_data';

/// Same consumption rules as web/domain.js and server/domain.py: a 300 ml bottle
/// is drunk whole, anything else one gō (180 ml) at a time.
class ConsumptionPolicy {
  static int unit(int volume) => volume == 300 ? 300 : 180;
  static String label(int volume) => volume == 300 ? '1本' : '1合';
}

class Fridge {
  const Fridge({required this.id, required this.name});

  factory Fridge.fromJson(Map<String, dynamic> json) =>
      Fridge(id: json['id'] as String, name: json['name'] as String);

  final String id;
  final String name;
}

class Bottle {
  const Bottle({
    required this.id,
    required this.name,
    required this.brewery,
    required this.volume,
    required this.remaining,
    required this.fridgeId,
    required this.shelf,
    required this.version,
    this.category = '',
    this.productionDate = '',
    this.photo = '',
    this.openedAt,
  });

  factory Bottle.fromJson(Map<String, dynamic> json) => Bottle(
        id: json['id'] as String,
        name: json['name'] as String,
        brewery: json['brewery'] as String? ?? '',
        category: json['category'] as String? ?? '',
        productionDate: json['productionDate'] as String? ?? '',
        volume: json['volume'] as int,
        remaining: json['remaining'] as int,
        fridgeId: json['fridgeId'] as String,
        shelf: json['shelf'] as String? ?? '',
        photo: json['photo'] as String? ?? '',
        openedAt: json['openedAt'] as String?,
        version: json['version'] as int,
      );

  final String id;
  final String name;
  final String brewery;
  final String category;
  final String productionDate;
  final int volume;
  final int remaining;
  final String fridgeId;
  final String shelf;
  final String photo;
  final String? openedAt;
  final int version;

  bool get empty => remaining == 0;

  /// Less than one serving left: the next action drinks the rest.
  bool get onlyRemainder => remaining > 0 && remaining < ConsumptionPolicy.unit(volume);

  String get actionLabel => onlyRemainder ? '残りを飲み切る' : '${ConsumptionPolicy.label(volume)}消費';

  String get remainingLabel {
    if (volume == 300) return remaining > 0 ? '1本' : '空瓶';
    final go = remaining / 180;
    final text = go.toStringAsFixed(2).replaceFirst(RegExp(r'\.?0+$'), '');
    return '$text合';
  }

  Uint8List? get photoBytes {
    const prefix = 'data:image/jpeg;base64,';
    if (!photo.startsWith(prefix)) return null;
    try {
      return base64Decode(photo.substring(prefix.length));
    } on FormatException {
      return null;
    }
  }
}

class InventoryEvent {
  const InventoryEvent({required this.type, required this.at, required this.actor, required this.name, this.amount});

  factory InventoryEvent.fromJson(Map<String, dynamic> json) => InventoryEvent(
        type: json['type'] as String,
        at: DateTime.parse(json['at'] as String).toLocal(),
        actor: json['actor'] as String? ?? '',
        name: json['name'] as String? ?? '',
        amount: json['amount'] as int?,
      );

  static const labels = {
    'receive': '入荷',
    'consume': '消費',
    'finish': '飲み切り',
    'move': '移動',
    'undo': '取り消し',
    'addFridge': '冷蔵庫追加',
    'renameFridge': '冷蔵庫名変更',
    'deleteFridge': '冷蔵庫削除',
    'importPersonal': '個人データ移行',
  };

  final String type;
  final DateTime at;
  final String actor;
  final String name;
  final int? amount;

  String get label => labels[type] ?? type;
}

class InventoryState {
  const InventoryState({required this.fridges, required this.bottles, required this.events});

  factory InventoryState.fromJson(Map<String, dynamic> json) => InventoryState(
        fridges: [for (final f in json['refrigerators'] as List) Fridge.fromJson(f as Map<String, dynamic>)],
        bottles: [for (final b in json['bottles'] as List) Bottle.fromJson(b as Map<String, dynamic>)],
        events: [for (final e in json['events'] as List) InventoryEvent.fromJson(e as Map<String, dynamic>)],
      );

  static const empty = InventoryState(fridges: [], bottles: [], events: []);

  final List<Fridge> fridges;
  final List<Bottle> bottles;
  final List<InventoryEvent> events;

  String fridgeName(String id) {
    for (final f in fridges) {
      if (f.id == id) return f.name;
    }
    return '削除済みの冷蔵庫';
  }

  List<Bottle> filter({String query = '', String? fridgeId, bool showEmpty = false}) {
    final q = query.trim().toLowerCase();
    return [
      for (final b in bottles)
        if ((showEmpty || !b.empty) &&
            (fridgeId == null || b.fridgeId == fridgeId) &&
            '${b.name} ${b.brewery}'.toLowerCase().contains(q))
          b,
    ];
  }
}

final _random = Random.secure();

/// Operation and bottle IDs; the server uses the operation ID to ignore retries.
String newId() => List.generate(16, (_) => _random.nextInt(256).toRadixString(16).padLeft(2, '0')).join();

Map<String, dynamic> command(String type, Map<String, dynamic> payload) => {
      'id': newId(),
      'type': type,
      'payload': payload,
      'at': DateTime.now().toUtc().toIso8601String(),
    };
