'use strict';
const report = JSON.parse(document.getElementById('offer-data').textContent);
const byId = id => document.getElementById(id);
const selectedKeys = new Set(report.recommended);
const pageSize = 24;
let filter = 'all', page = 1, filtered = [];
const number = value => new Intl.NumberFormat('zh-CN').format(value);
const time = epoch => new Intl.DateTimeFormat('zh-CN', {
  timeZone: 'Asia/Shanghai', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false
}).format(new Date(epoch * 1000));
const textNode = (tag, className, content) => {
  const node = document.createElement(tag);
  node.className = className;
  node.textContent = content;
  return node;
};
byId('capture').textContent = '采集时间 ' + report.day + ' ' + time(Date.parse(report.captured_at) / 1000).split(' ')[1];
const freeCount = report.offers.filter(item => item.free).length;
byId('free-count').textContent = number(freeCount);
byId('discount-count').textContent = number(report.offers.length - freeCount);
byId('all-tab').textContent = number(report.offers.length);
byId('free-tab').textContent = number(freeCount);
byId('threshold').options[0].value = String(report.minimum_discount);
byId('threshold').options[0].textContent = report.minimum_discount + '%';
const wantedDay = new URLSearchParams(location.search).get('day');
const today = new Intl.DateTimeFormat('sv-SE', {timeZone: 'Asia/Shanghai'}).format(new Date());
if (report.day !== today || (wantedDay && wantedDay !== report.day)) {
  byId('notice').hidden = false;
  byId('notice').textContent = '当前展示 ' + report.day + ' 的采集快照。网页每天更新，旧微信链接也会打开最新名单；请以 Steam 当前价格和领取状态为准。';
}
for (const warning of report.warnings) byId('warnings').append(textNode('li', '', warning));

function card(item) {
  const link = document.createElement('a');
  link.className = 'card';
  link.href = item.url;
  link.target = '_blank';
  link.rel = 'noopener noreferrer';
  const cover = textNode('div', 'cover', '');
  const placeholder = textNode('span', 'placeholder', 'STEAM');
  cover.append(placeholder);
  if (item.image_url) {
    const image = document.createElement('img');
    image.alt = item.name + ' 封面';
    image.loading = 'lazy';
    image.decoding = 'async';
    image.referrerPolicy = 'no-referrer';
    image.addEventListener('load', () => { placeholder.hidden = true; });
    image.addEventListener('error', () => { image.remove(); placeholder.hidden = false; }, {once: true});
    image.src = item.image_url;
    cover.append(image);
    placeholder.hidden = true;
  }
  const expired = item.free && item.end_epoch && item.end_epoch * 1000 <= Date.now();
  cover.append(textNode('span', 'badge' + (expired ? ' expired' : ''),
    item.free ? (expired ? '已到截止时间' : '限时免费') : '-' + item.discount + '%'));
  if (selectedKeys.has(item.key)) cover.append(textNode('span', 'featured-badge', '精选'));
  const body = textNode('div', 'card-body', '');
  const kind = item.key.startsWith('sub:') ? '礼包' : item.key.startsWith('bundle:') ? '捆绑包' : 'Steam 商品';
  body.append(textNode('div', 'kind', kind), textNode('h3', '', item.name));
  const prices = textNode('div', 'prices', '');
  prices.append(textNode('span', 'current', item.free ? '免费领取' : item.current));
  if (item.original !== '未知') prices.append(textNode('span', 'original', item.original));
  body.append(prices);
  const review = item.review_positive !== null && item.review_count !== null
    ? item.review_positive + '% 好评 · ' + number(item.review_count) + ' 篇评测' : '评测数据未公开';
  body.append(textNode('div', 'review', review));
  const urgent = item.free && item.end_epoch && item.end_epoch * 1000 > Date.now()
    && item.end_epoch * 1000 - Date.now() <= 86400000;
  const deadline = item.end_epoch ? '结束 ' + time(item.end_epoch) + (urgent ? ' · 24小时内' : '') : '结束时间未公开';
  body.append(textNode('div', 'deadline' + (urgent ? ' urgent' : ''), deadline));
  if (item.start_epoch) body.append(textNode('div', 'raw-note', '开始 ' + time(item.start_epoch)));
  if (item.deadline_note && !item.end_epoch) body.append(textNode('div', 'raw-note', item.deadline_note + '（时区未核实）'));
  body.append(textNode('div', 'store-link', '前往 Steam 商店 ↗'));
  link.append(cover, body);
  return link;
}

function applyFilters() {
  const search = byId('search').value.trim().toLocaleLowerCase();
  const minimum = Number(byId('threshold').value);
  filtered = report.offers.filter(item => {
    const kind = filter === 'all' || (filter === 'free' && item.free)
      || (filter === 'discount' && !item.free) || (filter === 'featured' && selectedKeys.has(item.key));
    return kind && item.discount >= minimum && item.name.toLocaleLowerCase().includes(search);
  });
  const sort = byId('sort').value;
  const rank = item => item.free ? 0 : selectedKeys.has(item.key) ? 1 : 2;
  filtered.sort((a, b) => {
    let result = 0;
    if (sort === 'featured') result = rank(a) - rank(b);
    if (sort === 'price') result = a.final_cents - b.final_cents;
    if (sort === 'reviews') result = (b.review_count || 0) - (a.review_count || 0);
    if (sort === 'deadline') result = (a.end_epoch || Infinity) - (b.end_epoch || Infinity);
    if (sort === 'name') result = a.name.localeCompare(b.name, 'zh-CN');
    return result || b.discount - a.discount || a.final_cents - b.final_cents || a.key.localeCompare(b.key);
  });
  page = 1;
  render();
}

function render() {
  const pages = Math.max(1, Math.ceil(filtered.length / pageSize));
  const fragment = document.createDocumentFragment();
  for (const item of filtered.slice((page - 1) * pageSize, page * pageSize)) fragment.append(card(item));
  byId('grid').replaceChildren(fragment);
  byId('result-count').textContent = '找到 ' + number(filtered.length) + ' 项商品';
  byId('page-label').textContent = page + ' / ' + pages;
  byId('prev').disabled = page <= 1;
  byId('next').disabled = page >= pages;
  byId('empty').hidden = filtered.length > 0;
}

for (const button of document.querySelectorAll('[data-filter]')) {
  button.addEventListener('click', () => {
    filter = button.dataset.filter;
    for (const tab of document.querySelectorAll('[data-filter]')) {
      tab.classList.toggle('active', tab === button);
      tab.setAttribute('aria-pressed', String(tab === button));
    }
    applyFilters();
  });
}
byId('search').addEventListener('input', applyFilters);
byId('sort').addEventListener('change', applyFilters);
byId('threshold').addEventListener('change', applyFilters);
for (const [id, delta] of [['prev', -1], ['next', 1]]) {
  byId(id).addEventListener('click', () => {
    page += delta;
    render();
    document.querySelector('.catalog').scrollIntoView({behavior: 'smooth', block: 'start'});
  });
}
applyFilters();
