import 'package:bacchus_mobile/src/api.dart';
import 'package:bacchus_mobile/src/inventory.dart';
import 'package:flutter_test/flutter_test.dart';

Map<String, dynamic> bottle(String id, {int volume = 720, int remaining = 720, String fridgeId = 'f', String name = '試験酒'}) => {
      'id': id,
      'name': name,
      'brewery': '試験酒造',
      'category': '',
      'productionDate': '',
      'volume': volume,
      'remaining': remaining,
      'fridgeId': fridgeId,
      'shelf': '',
      'photo': '',
      'receivedAt': '2026-10-01T00:00:00+00:00',
      'openedAt': null,
      'version': 1,
    };

void main() {
  test('consumption units and labels match the web and server rules', () {
    expect(ConsumptionPolicy.unit(300), 300);
    expect(ConsumptionPolicy.unit(720), 180);
    expect(Bottle.fromJson(bottle('a')).remainingLabel, '4合');
    expect(Bottle.fromJson(bottle('a', remaining: 540)).remainingLabel, '3合');
    expect(Bottle.fromJson(bottle('a', volume: 500, remaining: 140)).remainingLabel, '0.78合');
    expect(Bottle.fromJson(bottle('a', volume: 1800, remaining: 90)).remainingLabel, '0.5合');
    expect(Bottle.fromJson(bottle('a', volume: 300, remaining: 300)).remainingLabel, '1本');
    expect(Bottle.fromJson(bottle('a', volume: 300, remaining: 0)).remainingLabel, '空瓶');
    expect(Bottle.fromJson(bottle('a', volume: 500, remaining: 140)).actionLabel, '残りを飲み切る');
    expect(Bottle.fromJson(bottle('a', volume: 300)).actionLabel, '1本消費');
  });

  test('state parsing, deleted fridges and filters', () {
    final state = InventoryState.fromJson({
      'refrigerators': [
        {'id': 'f', 'name': '本店'},
      ],
      'bottles': [bottle('a', name: '獺祭'), bottle('b', remaining: 0), bottle('c', fridgeId: 'gone', name: '十四代')],
      'events': [
        {'id': 'e', 'type': 'consume', 'at': '2026-10-02T00:00:00+00:00', 'actor': '花子', 'name': '獺祭', 'amount': 180},
      ],
    });
    expect(state.fridgeName('gone'), '削除済みの冷蔵庫');
    expect(state.filter().map((b) => b.id), ['a', 'c']);
    expect(state.filter(showEmpty: true).length, 3);
    expect(state.filter(query: '十四').map((b) => b.id), ['c']);
    expect(state.filter(fridgeId: 'f').map((b) => b.id), ['a']);
    expect(state.events.single.label, '消費');
  });

  test('commands carry unique IDs and UTC timestamps', () {
    final a = command('consume', {'bottleId': 'a', 'version': 1});
    final b = command('consume', {'bottleId': 'a', 'version': 1});
    expect(a['id'], isNot(b['id']));
    expect((a['id'] as String).length, 32);
    expect((a['at'] as String).endsWith('Z'), isTrue);
  });

  test('server addresses require HTTPS except local development', () {
    expect(ApiClient.parseServer('sake.example.com').toString(), 'https://sake.example.com');
    expect(ApiClient.parseServer(' https://sake.example.com/app?x=1 ').host, 'sake.example.com');
    expect(ApiClient.parseServer('http://10.0.2.2:8000').port, 8000);
    expect(() => ApiClient.parseServer('http://sake.example.com'), throwsFormatException);
    expect(() => ApiClient.parseServer(''), throwsFormatException);
  });
}
