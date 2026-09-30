import test from 'node:test';
import assert from 'node:assert/strict';
import { matchWakeWord, isStopIntent, speechLangFor, stripForSpeech, takeSentences } from '../../ui/js/voice-logic.js';

test('wake word: start, command extraction, recogniser spelling variants', () => {
  assert.deepEqual(matchWakeWord('Hey M.R.X., open Chrome'), { command: 'open chrome', matched: true });
  assert.equal(matchWakeWord('hey mrx').command, '');                 // wake word alone → wait for the command
  assert.equal(matchWakeWord('hey mr x play some music').command, 'play some music');
  assert.equal(matchWakeWord('okay m r x what time is it').command, 'what time is it');
  assert.equal(matchWakeWord('open chrome please'), null);           // no wake word → ignored
  assert.equal(matchWakeWord('the mr x pole'), null);                // bare "mr x" without a greeting must not trigger
  assert.equal(matchWakeWord('hey jarvis open mail', 'hey jarvis').command, 'open mail');   // configurable
});

test('interruption / barge-in intents, incl. Hindi and other languages', () => {
  for (const t of ['stop', 'Stop.', 'cancel', 'hey mrx stop', 'ruko', 'रुको', 'बस', 'ਰੁਕੋ', 'please stop', 'stop it']) assert.equal(isStopIntent(t), true, t);
  for (const t of ['open chrome', 'stopwatch timer', 'bus schedule', 'play stop and stare']) assert.equal(isStopIntent(t), false, t);
});

test('speech language follows the reply script', () => {
  assert.equal(speechLangFor('क्रोम खोल रहा हूँ'), 'hi-IN'); assert.equal(speechLangFor('ਕਰੋਮ ਖੋਲ੍ਹ ਰਿਹਾ ਹਾਂ'), 'pa-IN');
  assert.equal(speechLangFor('குரோம் திறக்கிறேன்'), 'ta-IN'); assert.equal(speechLangFor('Opening Chrome', 'en-GB'), 'en-GB');
});

test('speech text is cleaned of markup, glyphs and URLs', () => {
  assert.equal(stripForSpeech('✓ Opened **Chrome** (verified)'), 'Opened Chrome (verified)');
  assert.equal(stripForSpeech('See https://example.com/x now'), 'See link now');
});

test('streaming TTS emits whole sentences and keeps the tail', () => {
  let r = takeSentences('Opening Chrome. Then I will sear');
  assert.deepEqual(r.sentences, ['Opening Chrome.']); assert.equal(r.rest, ' Then I will sear');
  r = takeSentences('Done! ठीक है। और');
  assert.deepEqual(r.sentences, ['Done!', 'ठीक है।']); assert.equal(r.rest, ' और');
  assert.deepEqual(takeSentences('no terminator yet').sentences, []);
});
