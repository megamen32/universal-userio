import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const MarkdownIt = require('markdown-it');
const { HTMLParser } = require('telegram/extensions/html.js');
const markdown = new MarkdownIt({html: false, linkify: false, breaks: true});
markdown.renderer.rules.heading_open = () => '<b>';
markdown.renderer.rules.heading_close = () => '</b>\n\n';
markdown.renderer.rules.paragraph_open = () => '';
markdown.renderer.rules.paragraph_close = (tokens, i) => tokens[i].hidden ? '' : '\n\n';
markdown.renderer.rules.softbreak = markdown.renderer.rules.hardbreak = () => '\n';
markdown.renderer.rules.hr = () => '';
markdown.renderer.rules.bullet_list_open = (_t, _i, _o, env) => { env.lists.push(null); return ''; };
markdown.renderer.rules.ordered_list_open = (tokens, i, _o, env) => { env.lists.push(Number(tokens[i].attrGet('start') ?? 1)); return ''; };
markdown.renderer.rules.bullet_list_close = markdown.renderer.rules.ordered_list_close = (_t, _i, _o, env) => { env.lists.pop(); return '\n'; };
markdown.renderer.rules.list_item_open = (_t, _i, _o, env) => {
  const i = env.lists.length - 1;
  return '  '.repeat(Math.max(0, i)) + (env.lists[i] === null ? '• ' : `${env.lists[i]++}. `);
};
markdown.renderer.rules.list_item_close = () => '\n';
markdown.renderer.rules.image = (tokens, i) => markdown.utils.escapeHtml(tokens[i].content);
markdown.renderer.rules.table_open = markdown.renderer.rules.tbody_open = () => '';
markdown.renderer.rules.table_close = markdown.renderer.rules.tbody_close = () => '\n';
markdown.renderer.rules.thead_open = () => '<b>';
markdown.renderer.rules.thead_close = () => '</b>\n';
markdown.renderer.rules.tr_open = markdown.renderer.rules.th_open = markdown.renderer.rules.td_open = () => '';
markdown.renderer.rules.tr_close = () => '\n';
markdown.renderer.rules.th_close = markdown.renderer.rules.td_close = (tokens, i) => tokens[i + 1]?.type === 'tr_close' ? '' : ' | ';

// Formatting is opt-in and is bound to the approved UserIO action.
export function formatTelegramMessage(source, format) {
  if (format === undefined) return {text: source, entities: []};
  if (format !== 'telegram_markdown') throw new Error('unsupported Telegram text format');
  const html = markdown.render(source, {lists: []}).trimEnd();
  const [wrapped, entities] = HTMLParser.parse('\uE000' + html + '\uE001');
  for (const entity of entities) entity.offset -= 1;
  return {text: wrapped.slice(1, -1), entities: entities.filter(entity => entity.length > 0)};
}

export function sameTelegramEntities(expected, actual) {
  const view = entity => ({type: entity.className, offset: entity.offset, length: entity.length,
    ...(entity.url ? {url: entity.url} : {}), ...(entity.language ? {language: entity.language} : {})});
  const automatic = new Set(['MessageEntityUrl', 'MessageEntityEmail', 'MessageEntityMention',
    'MessageEntityHashtag', 'MessageEntityBotCommand', 'MessageEntityCashtag', 'MessageEntityPhone']);
  const normalize = items => items.map(view).filter(item => !automatic.has(item.type))
    .map(item => JSON.stringify(item)).sort();
  return JSON.stringify(normalize(expected)) === JSON.stringify(normalize(actual || []));
}
