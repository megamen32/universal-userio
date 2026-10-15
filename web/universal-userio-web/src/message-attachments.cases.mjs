// fast unit; purpose: preserve every file/index and enforce the composer limit.
// Detected defects: only the first attachment was rendered; MAX picker was hidden.
// Expected 1 second; emergency maximum 10 seconds. Browser proof belongs to lead.
import assert from 'node:assert/strict'
import test from 'node:test'
import { attachmentMediaPath, isImageAttachment, messageAttachments, supportsFileUpload, uploadValidationError } from './message-attachments.ts'

test('all files survive with provider indexes, names and byte sizes', () => {
  assert.deepEqual(messageAttachments([
    { kind: 'document', filename: 'Первый.pdf', size: 480142 },
    null,
    { kind: 'document', filename: 'Второй.pdf', size: 780937 },
  ]).map(({ index, filename, size }) => ({ index, filename, size })), [
    { index: 0, filename: 'Первый.pdf', size: 480142 },
    { index: 2, filename: 'Второй.pdf', size: 780937 },
  ])
})

test('link previews do not displace files or change their provider index', () => {
  assert.deepEqual(messageAttachments([{ kind: 'share' }, { kind: 'document', name: 'Каталог.pdf', file_size: 100 }]).map(a => [a.index, a.filename, a.size]), [[1, 'Каталог.pdf', 100]])
})

test('preview and downloads address the selected attachment', () => {
  assert.equal(attachmentMediaPath('conv', 'message/id', 2), '/v1/conversations/conv/media/message%2Fid?attachment_index=2')
  assert.equal(attachmentMediaPath('conv', 'message/id', 2, true), '/v1/conversations/conv/media/message%2Fid/raw?attachment_index=2')
})

test('photos are identified from type or kind without treating PDF as image', () => {
  const items = messageAttachments([{ kind: 'photo' }, { kind: 'file', mime_type: 'image/png' }, { kind: 'document', mime_type: 'application/pdf' }])
  assert.deepEqual(items.map(isImageAttachment), [true, true, false])
})

test('native MAX display fields retain file names and image type', () => {
  const [file] = messageAttachments([{ _type: 'FILE', fileName: 'Картинка.png', mimeType: 'image/png', size: 68 }])
  assert.equal(file.filename, 'Картинка.png')
  assert.equal(file.kind, 'file')
  assert.equal(isImageAttachment(file), true)
})

test('generic provider MIME uses safe image extensions after reload', () => {
  const items = messageAttachments([
    { kind: 'file', filename: 'Картинка.PNG', content_type: 'application/octet-stream' },
    { kind: 'file', filename: 'Фото.jpeg' },
    { kind: 'file', filename: 'Каталог.pdf', content_type: 'application/octet-stream' },
    { kind: 'file', filename: 'Фото.png.exe', content_type: 'application/octet-stream' },
    { kind: 'file', filename: 'Фото.png', content_type: 'application/pdf' },
    { kind: 'file', filename: 'Рисунок.svg', content_type: 'application/octet-stream' },
  ])
  assert.deepEqual(items.map(isImageAttachment), [true, true, false, false, false, false])
})

test('MAX and existing VK personal accounts allow files; unsupported accounts do not', () => {
  assert.equal(supportsFileUpload('max', 'max'), true)
  assert.equal(supportsFileUpload('max', 'max:personal'), true)
  assert.equal(supportsFileUpload('vk', 'vk:123'), true)
  assert.equal(supportsFileUpload('vk', 'vk:bot'), false)
  assert.equal(supportsFileUpload('telegram', 'telegram'), false)
})

test('empty and over-limit uploads fail before reading bytes; exact 2 MiB succeeds', () => {
  assert.match(uploadValidationError(0), /пустой/)
  assert.equal(uploadValidationError(2 * 1024 * 1024), '')
  assert.match(uploadValidationError(2 * 1024 * 1024 + 1), /2 МБ/)
})
