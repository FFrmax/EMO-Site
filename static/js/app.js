// ============ STATE ============
let socket = null;
let allContacts = { saved: [], unsaved: [] };
let currentChat = null;
let typingTimeout = null;
let selectedTheme = CURRENT_USER.theme || 'dark';

// ============ INIT ============
document.addEventListener('DOMContentLoaded', () => {
    initSocket();
    loadContacts();
    initProfileColor();
    initThemePicker();
});

function initSocket() {
    socket = io({ transports: ['websocket', 'polling'], reconnection: true, reconnectionDelay: 1000 });
    socket.on('connect', () => console.log('EMO: connected'));
    socket.on('disconnect', () => console.log('EMO: disconnected'));
    socket.on('new_message', handleNewMessage);
    socket.on('user_status', handleUserStatus);
    socket.on('user_typing', handleUserTyping);
    socket.on('user_stop_typing', handleUserStopTyping);
    socket.on('messages_read', handleMessagesRead);
}

// ============ CONTACTS ============
async function loadContacts() {
    try {
        const res = await fetch('/api/contacts');
        allContacts = await res.json();
        renderContacts();
    } catch (e) { console.error('Kisiler yuklenemedi:', e); }
}

function renderContacts() {
    const list = document.getElementById('contactsList');
    const empty = document.getElementById('emptyContacts');
    const search = document.getElementById('searchContacts').value.toLowerCase();

    // clear old items
    list.querySelectorAll('.contact-item,.contacts-section-label').forEach(e => e.remove());

    const filterFn = c =>
        c.display_name.toLowerCase().includes(search) ||
        (c.real_name && c.real_name.toLowerCase().includes(search)) ||
        c.emo_id.toLowerCase().includes(search);

    const saved = allContacts.saved.filter(filterFn);
    const unsaved = allContacts.unsaved.filter(filterFn);

    if (saved.length === 0 && unsaved.length === 0) {
        empty.style.display = allContacts.saved.length === 0 && allContacts.unsaved.length === 0 ? 'flex' : 'none';
        if (search && (allContacts.saved.length > 0 || allContacts.unsaved.length > 0)) {
            empty.style.display = 'none';
        }
        return;
    }
    empty.style.display = 'none';

    // Saved contacts
    if (saved.length > 0 && unsaved.length > 0) {
        const label = document.createElement('div');
        label.className = 'contacts-section-label';
        label.textContent = 'KAYITLI';
        list.appendChild(label);
    }
    saved.forEach(c => list.appendChild(createContactItem(c)));

    // Unsaved contacts
    if (unsaved.length > 0) {
        const label = document.createElement('div');
        label.className = 'contacts-section-label';
        label.textContent = 'KAYITLI DEGIL';
        list.appendChild(label);
        unsaved.forEach(c => list.appendChild(createContactItem(c)));
    }
}

function createContactItem(contact) {
    const item = document.createElement('div');
    item.className = 'contact-item' + (currentChat && currentChat.user_id === contact.user_id ? ' active' : '');
    item.dataset.userId = contact.user_id;

    const timeStr = contact.last_message_time ? formatTime(contact.last_message_time) : '';
    const lastMsg = contact.last_message || '';
    const truncMsg = lastMsg.length > 28 ? lastMsg.substring(0, 28) + '...' : lastMsg;

    // Avatar
    let avatarHtml;
    if (contact.avatar_image) {
        avatarHtml = `<img class="avatar-img" src="${contact.avatar_image}" alt="">`;
    } else {
        avatarHtml = `<div class="avatar" style="background:${contact.avatar_color}">${(contact.display_name || '?')[0].toUpperCase()}</div>`;
    }

    // Name display
    let nameHtml = `<span class="contact-name">${escapeHtml(contact.display_name)}</span>`;
    let realNameHtml = '';
    if (contact.is_saved && contact.nickname && contact.real_name && contact.nickname !== contact.real_name) {
        realNameHtml = `<div class="contact-real-name">${escapeHtml(contact.real_name)}</div>`;
    }

    // Unsaved badge
    let badgeHtml = '';
    if (!contact.is_saved) {
        badgeHtml = `<span class="unsaved-badge">Kayitli degil</span>`;
    }

    // Save button for unsaved
    let saveBtnHtml = '';
    if (!contact.is_saved) {
        saveBtnHtml = `<button class="contact-save-btn" onclick="event.stopPropagation();quickSaveContact(${contact.user_id},'${escapeHtml(contact.emo_id)}')">Kaydet</button>`;
    }

    item.innerHTML = `
        <div class="contact-avatar">
            ${avatarHtml}
            ${contact.is_online ? '<div class="online-dot"></div>' : ''}
        </div>
        <div class="contact-info">
            <div class="contact-name-row">
                ${nameHtml}
                <span class="contact-time">${timeStr}</span>
            </div>
            ${realNameHtml}
            ${badgeHtml}
            <div class="contact-last-msg-row">
                <span class="contact-last-msg">${escapeHtml(truncMsg)}</span>
                ${contact.unread_count > 0 ? `<span class="unread-badge">${contact.unread_count}</span>` : ''}
            </div>
        </div>
        ${saveBtnHtml}
    `;

    item.onclick = () => openChat(contact);
    return item;
}

function filterContacts() { renderContacts(); }

async function quickSaveContact(userId, emoId) {
    try {
        const res = await fetch('/api/contacts', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ user_id: userId, emo_id: emoId })
        });
        const data = await res.json();
        if (data.success) {
            showToast(data.message, 'success');
            await loadContacts();
            if (currentChat && currentChat.user_id === userId) {
                const c = allContacts.saved.find(x => x.user_id === userId);
                if (c) {
                    currentChat = c;
                    updateChatHeader();
                }
            }
        } else {
            showToast(data.error, 'error');
        }
    } catch (e) { showToast('Hata olustu', 'error'); }
}

// ============ CHAT ============
async function openChat(contact) {
    currentChat = contact;
    document.getElementById('noChatSelected').style.display = 'none';
    document.getElementById('activeChat').style.display = 'flex';

    updateChatHeader();

    // mark active in sidebar
    document.querySelectorAll('.contact-item').forEach(el => {
        el.classList.toggle('active', parseInt(el.dataset.userId) === contact.user_id);
    });

    await loadMessages(contact.user_id);
    socket.emit('mark_read', { sender_id: contact.user_id });
    contact.unread_count = 0;
    renderContacts();

    document.getElementById('messageInput').focus();
    if (window.innerWidth <= 768) {
        document.getElementById('sidebar').classList.add('hidden');
    }
}

function updateChatHeader() {
    const c = currentChat;
    document.getElementById('chatHeaderName').textContent = c.display_name;

    // Show real name if nickname is different
    const realNameEl = document.getElementById('chatHeaderRealName');
    if (c.nickname && c.real_name && c.nickname !== c.real_name) {
        realNameEl.textContent = c.real_name;
        realNameEl.style.display = 'block';
    } else {
        realNameEl.style.display = 'none';
    }

    // Status
    const statusEl = document.getElementById('chatHeaderStatus');
    if (c.is_online) {
        statusEl.textContent = 'Cevrimici';
        statusEl.className = 'chat-header-status online';
    } else {
        statusEl.textContent = c.last_seen ? 'Son gorulme: ' + formatDateTime(c.last_seen) : c.emo_id;
        statusEl.className = 'chat-header-status';
    }

    // Avatar
    const avatarEl = document.getElementById('chatAvatar');
    if (c.avatar_image) {
        avatarEl.innerHTML = `<img class="avatar-img" src="${c.avatar_image}" alt="" style="width:42px;height:42px;min-width:42px">`;
    } else {
        avatarEl.innerHTML = `<div class="avatar" style="background:${c.avatar_color}">${c.display_name[0].toUpperCase()}</div>`;
    }

    // Save button
    const saveBtn = document.getElementById('saveFromChatBtn');
    saveBtn.style.display = c.is_saved ? 'none' : 'flex';
}

async function loadMessages(userId) {
    try {
        const res = await fetch(`/api/messages/${userId}?page=1`);
        const data = await res.json();
        const container = document.getElementById('messagesList');
        container.innerHTML = '';
        renderMessages(data.messages);
        scrollToBottom();
    } catch (e) { console.error('Mesajlar yuklenemedi:', e); }
}

function renderMessages(messages) {
    const container = document.getElementById('messagesList');
    let lastDate = null;
    messages.forEach(msg => {
        const d = formatDate(msg.timestamp);
        if (d !== lastDate) {
            lastDate = d;
            const div = document.createElement('div');
            div.className = 'message-date-divider';
            div.innerHTML = `<span>${d}</span>`;
            container.appendChild(div);
        }
        container.appendChild(createMessageEl(msg));
    });
}

function createMessageEl(msg) {
    const isSent = msg.sender_id === CURRENT_USER.id;
    const div = document.createElement('div');
    div.className = `message ${isSent ? 'sent' : 'received'}`;
    div.dataset.messageId = msg.id;
    const readIcon = isSent
        ? `<span class="read-check ${msg.is_read ? '' : 'unread'}">${msg.is_read ? '&#10003;&#10003;' : '&#10003;'}</span>`
        : '';
    div.innerHTML = `
        <div class="message-bubble">${escapeHtml(msg.content)}</div>
        <div class="message-time">${formatTime(msg.timestamp)} ${readIcon}</div>
    `;
    return div;
}

function scrollToBottom() {
    const c = document.getElementById('messagesContainer');
    requestAnimationFrame(() => { c.scrollTop = c.scrollHeight; });
}

// ============ SEND ============
function sendMessage() {
    const input = document.getElementById('messageInput');
    const content = input.value.trim();
    if (!content || !currentChat) return;
    socket.emit('send_message', { receiver_id: currentChat.user_id, content });
    socket.emit('stop_typing', { receiver_id: currentChat.user_id });
    input.value = '';
    input.style.height = 'auto';
    document.getElementById('sendBtn').disabled = true;
}

function handleKeyDown(e) {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
}

function handleInput() {
    const input = document.getElementById('messageInput');
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 120) + 'px';
    document.getElementById('sendBtn').disabled = !input.value.trim();

    if (currentChat && input.value.trim()) {
        socket.emit('typing', { receiver_id: currentChat.user_id });
        if (typingTimeout) clearTimeout(typingTimeout);
        typingTimeout = setTimeout(() => socket.emit('stop_typing', { receiver_id: currentChat.user_id }), 2000);
    }
}

// ============ SOCKET HANDLERS ============
function handleNewMessage(data) {
    if (currentChat &&
        ((data.sender_id === currentChat.user_id && data.receiver_id === CURRENT_USER.id) ||
         (data.sender_id === CURRENT_USER.id && data.receiver_id === currentChat.user_id))) {
        document.getElementById('messagesList').appendChild(createMessageEl(data));
        scrollToBottom();
        if (data.sender_id === currentChat.user_id) socket.emit('mark_read', { sender_id: currentChat.user_id });
    }

    const otherId = data.sender_id === CURRENT_USER.id ? data.receiver_id : data.sender_id;
    let contact = allContacts.saved.find(c => c.user_id === otherId) || allContacts.unsaved.find(c => c.user_id === otherId);
    if (contact) {
        contact.last_message = data.content;
        contact.last_message_time = data.timestamp;
        if (data.sender_id !== CURRENT_USER.id && (!currentChat || currentChat.user_id !== data.sender_id)) {
            contact.unread_count = (contact.unread_count || 0) + 1;
        }
        renderContacts();
    } else {
        loadContacts();
    }
}

function handleUserStatus(data) {
    const update = c => { if (c.user_id === data.user_id) c.is_online = data.is_online; };
    allContacts.saved.forEach(update);
    allContacts.unsaved.forEach(update);
    renderContacts();
    if (currentChat && currentChat.user_id === data.user_id) {
        currentChat.is_online = data.is_online;
        updateChatHeader();
    }
}

function handleUserTyping(data) {
    if (currentChat && currentChat.user_id === data.user_id) {
        document.getElementById('typingIndicator').style.display = 'flex';
        scrollToBottom();
    }
}

function handleUserStopTyping(data) {
    if (currentChat && currentChat.user_id === data.user_id)
        document.getElementById('typingIndicator').style.display = 'none';
}

function handleMessagesRead(data) {
    if (currentChat && currentChat.user_id === data.reader_id) {
        document.querySelectorAll('.message.sent .read-check').forEach(el => {
            el.classList.remove('unread');
            el.innerHTML = '&#10003;&#10003;';
        });
    }
}

// ============ ADD CONTACT MODAL ============
function openAddContactModal() {
    document.getElementById('addContactEmoId').value = '';
    document.getElementById('addContactNickname').value = '';
    document.getElementById('searchResult').style.display = 'none';
    document.getElementById('searchError').style.display = 'none';
    document.getElementById('addContactModal').classList.add('active');
    setTimeout(() => document.getElementById('addContactEmoId').focus(), 100);
}

let searchTimer = null;
function searchUserById() {
    const input = document.getElementById('addContactEmoId').value.trim();
    document.getElementById('searchResult').style.display = 'none';
    document.getElementById('searchError').style.display = 'none';
    if (input.length < 3) return;
    if (searchTimer) clearTimeout(searchTimer);
    searchTimer = setTimeout(async () => {
        try {
            const res = await fetch(`/api/user/search?emo_id=${encodeURIComponent(input)}`);
            const data = await res.json();
            if (data.error) {
                document.getElementById('searchError').textContent = data.error;
                document.getElementById('searchError').style.display = 'block';
                return;
            }
            const avatarEl = document.getElementById('searchResultAvatar');
            if (data.avatar_image) {
                avatarEl.innerHTML = `<img class="avatar-img" src="${data.avatar_image}" style="width:50px;height:50px;min-width:50px">`;
            } else {
                avatarEl.innerHTML = `<div class="avatar" style="background:${data.avatar_color};width:50px;height:50px;min-width:50px;font-size:20px">${data.display_name[0].toUpperCase()}</div>`;
            }
            document.getElementById('searchResultName').textContent = data.display_name;
            document.getElementById('searchResultId').textContent = data.emo_id;
            document.getElementById('searchResultStatus').textContent = data.status_text || '';

            const addBtn = document.getElementById('addContactBtn');
            const already = document.getElementById('alreadyContact');
            const nickField = document.querySelector('#searchResult .form-group');
            if (data.is_contact) {
                addBtn.style.display = 'none';
                nickField.style.display = 'none';
                already.style.display = 'block';
            } else {
                addBtn.style.display = 'block';
                nickField.style.display = 'block';
                already.style.display = 'none';
            }
            const sr = document.getElementById('searchResult');
            sr.style.display = 'block';
            sr.dataset.userId = data.id;
            sr.dataset.emoId = data.emo_id;
        } catch (e) {
            document.getElementById('searchError').textContent = 'Arama hatasi';
            document.getElementById('searchError').style.display = 'block';
        }
    }, 400);
}

async function addContact() {
    const emoId = document.getElementById('searchResult').dataset.emoId;
    const nickname = document.getElementById('addContactNickname').value.trim();
    try {
        const res = await fetch('/api/contacts', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ emo_id: emoId, nickname: nickname || undefined })
        });
        const data = await res.json();
        if (data.success) {
            showToast(data.message, 'success');
            closeModal('addContactModal');
            await loadContacts();
        } else { showToast(data.error, 'error'); }
    } catch (e) { showToast('Hata olustu', 'error'); }
}

function saveContactFromChat() {
    if (!currentChat) return;
    quickSaveContact(currentChat.user_id, currentChat.emo_id);
}

function saveContactFromInfo() {
    if (!currentChat) return;
    quickSaveContact(currentChat.user_id, currentChat.emo_id);
    closeModal('contactInfoModal');
}

// ============ PROFILE ============
function openProfileModal() {
    document.getElementById('profileModal').classList.add('active');
}

function initProfileColor() {
    document.querySelectorAll('#colorPicker .color-option').forEach(o => {
        if (o.dataset.color === CURRENT_USER.avatar_color) o.classList.add('selected');
    });
}

function selectColor(el) {
    el.closest('.color-picker').querySelectorAll('.color-option').forEach(o => o.classList.remove('selected'));
    el.classList.add('selected');
}

async function uploadAvatar(input) {
    if (!input.files || !input.files[0]) return;
    const file = input.files[0];
    if (file.size > 5 * 1024 * 1024) { showToast('Dosya 5MB\'dan kucuk olmali', 'error'); return; }

    const fd = new FormData();
    fd.append('avatar', file);

    try {
        const res = await fetch('/api/user/avatar', { method: 'POST', body: fd });
        const data = await res.json();
        if (data.success) {
            CURRENT_USER.avatar_image = data.avatar_url;
            // Update profile modal
            const container = document.querySelector('.avatar-upload-container');
            const existingImg = container.querySelector('.avatar-img');
            const existingDiv = container.querySelector('.profile-avatar-large:not(.avatar-img)');
            if (existingImg) {
                existingImg.src = data.avatar_url;
            } else {
                if (existingDiv) existingDiv.remove();
                const img = document.createElement('img');
                img.className = 'profile-avatar-large avatar-img';
                img.src = data.avatar_url;
                container.insertBefore(img, container.querySelector('.avatar-upload-overlay'));
            }
            // Update sidebar avatar
            updateSidebarAvatar();
            showToast('Profil resmi guncellendi', 'success');
        } else { showToast(data.error, 'error'); }
    } catch (e) { showToast('Yuklerken hata olustu', 'error'); }
}

function updateSidebarAvatar() {
    const mini = document.querySelector('.user-profile-mini');
    const oldAvatar = mini.querySelector('.avatar, .avatar-img');
    if (CURRENT_USER.avatar_image) {
        if (oldAvatar && oldAvatar.tagName === 'IMG') {
            oldAvatar.src = CURRENT_USER.avatar_image;
        } else {
            if (oldAvatar) oldAvatar.remove();
            const img = document.createElement('img');
            img.className = 'avatar avatar-img';
            img.src = CURRENT_USER.avatar_image;
            mini.insertBefore(img, mini.querySelector('.user-info-mini'));
        }
    } else {
        if (oldAvatar && oldAvatar.tagName === 'IMG') {
            const div = document.createElement('div');
            div.className = 'avatar';
            div.style.background = CURRENT_USER.avatar_color;
            div.textContent = CURRENT_USER.display_name[0].toUpperCase();
            oldAvatar.replaceWith(div);
        } else if (oldAvatar) {
            oldAvatar.style.background = CURRENT_USER.avatar_color;
            oldAvatar.textContent = CURRENT_USER.display_name[0].toUpperCase();
        }
    }
}

async function saveProfile() {
    const displayName = document.getElementById('profileDisplayName').value.trim();
    const statusText = document.getElementById('profileStatus').value.trim();
    const selColor = document.querySelector('#colorPicker .color-option.selected');
    const avatarColor = selColor ? selColor.dataset.color : CURRENT_USER.avatar_color;

    // get selected theme
    const selTheme = document.querySelector('#themePicker .theme-option.selected');
    const theme = selTheme ? selTheme.dataset.theme : selectedTheme;

    try {
        const res = await fetch('/api/user/me', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ display_name: displayName, status_text: statusText, avatar_color: avatarColor, theme })
        });
        const data = await res.json();
        if (data.success) {
            CURRENT_USER.display_name = data.user.display_name;
            CURRENT_USER.status_text = data.user.status_text;
            CURRENT_USER.avatar_color = data.user.avatar_color;
            CURRENT_USER.theme = data.user.theme;
            selectedTheme = data.user.theme;

            document.querySelector('.user-name-mini').textContent = displayName;
            updateSidebarAvatar();
            applyTheme(data.user.theme);

            showToast('Profil guncellendi', 'success');
            closeModal('profileModal');
        }
    } catch (e) { showToast('Hata olustu', 'error'); }
}

function copyEmoId() {
    const id = CURRENT_USER.emo_id;
    navigator.clipboard.writeText(id).then(
        () => showToast('EMO ID kopyalandi: ' + id, 'info'),
        () => { const t = document.createElement('textarea'); t.value = id; document.body.appendChild(t); t.select(); document.execCommand('copy'); document.body.removeChild(t); showToast('EMO ID kopyalandi: ' + id, 'info'); }
    );
}

// ============ THEME ============
function initThemePicker() {
    document.querySelectorAll('.theme-option').forEach(o => {
        if (o.dataset.theme === selectedTheme) o.classList.add('selected');
    });
}

function selectTheme(el) {
    // update all theme pickers
    document.querySelectorAll('.theme-option').forEach(o => o.classList.remove('selected'));
    document.querySelectorAll(`.theme-option[data-theme="${el.dataset.theme}"]`).forEach(o => o.classList.add('selected'));
    selectedTheme = el.dataset.theme;
    applyTheme(el.dataset.theme);

    // auto save
    fetch('/api/user/me', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ theme: el.dataset.theme })
    });
}

function applyTheme(theme) {
    document.body.setAttribute('data-theme', theme);
}

// ============ CONTACT INFO ============
function openContactInfoModal() {
    if (!currentChat) return;
    const c = currentChat;

    // avatar
    const wrap = document.getElementById('contactInfoAvatarWrap');
    if (c.avatar_image) {
        wrap.innerHTML = `<img class="profile-avatar-large avatar-img" src="${c.avatar_image}" style="width:88px;height:88px;margin:0 auto 14px;display:block">`;
    } else {
        wrap.innerHTML = `<div class="profile-avatar-large" style="background:${c.avatar_color};margin:0 auto 14px">${c.display_name[0].toUpperCase()}</div>`;
    }

    document.getElementById('contactInfoName').textContent = c.display_name;

    // show real name if different
    const realEl = document.getElementById('contactInfoRealName');
    if (c.nickname && c.real_name && c.nickname !== c.real_name) {
        realEl.textContent = c.real_name;
        realEl.style.display = 'block';
    } else {
        realEl.style.display = 'none';
    }

    document.getElementById('contactInfoEmoId').textContent = c.emo_id;
    document.getElementById('contactInfoStatus').textContent = c.status_text || '';
    document.getElementById('contactInfoLastSeen').textContent = c.is_online ? 'Cevrimici' : formatDateTime(c.last_seen);

    // saved vs unsaved sections
    if (c.is_saved) {
        document.getElementById('contactInfoSavedSection').style.display = 'block';
        document.getElementById('contactInfoUnsavedSection').style.display = 'none';
        document.getElementById('contactInfoNickname').value = c.nickname || '';
        document.getElementById('contactInfoAddedRow').style.display = 'flex';
        document.getElementById('contactInfoAddedAt').textContent = formatDateTime(c.added_at);
        document.getElementById('contactInfoDeleteBtn').style.display = 'block';
    } else {
        document.getElementById('contactInfoSavedSection').style.display = 'none';
        document.getElementById('contactInfoUnsavedSection').style.display = 'block';
        document.getElementById('contactInfoAddedRow').style.display = 'none';
        document.getElementById('contactInfoDeleteBtn').style.display = 'none';
    }

    document.getElementById('contactInfoModal').classList.add('active');
}

async function saveContactNickname() {
    if (!currentChat) return;
    const nickname = document.getElementById('contactInfoNickname').value.trim();
    try {
        const res = await fetch(`/api/contacts/${currentChat.user_id}/nickname`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ nickname })
        });
        const data = await res.json();
        if (data.success) {
            showToast('Takma ad guncellendi', 'success');
            closeModal('contactInfoModal');
            await loadContacts();
            const c = allContacts.saved.find(x => x.user_id === currentChat.user_id);
            if (c) { currentChat = c; updateChatHeader(); }
        }
    } catch (e) { showToast('Hata olustu', 'error'); }
}

async function deleteCurrentContact() {
    if (!currentChat || !currentChat.contact_id) return;
    if (!confirm(`${currentChat.display_name} kisiyi silmek istediginize emin misiniz?`)) return;
    try {
        const res = await fetch(`/api/contacts/${currentChat.contact_id}`, { method: 'DELETE' });
        const data = await res.json();
        if (data.success) {
            showToast('Kisi silindi', 'success');
            closeModal('contactInfoModal');
            currentChat = null;
            document.getElementById('activeChat').style.display = 'none';
            document.getElementById('noChatSelected').style.display = 'flex';
            await loadContacts();
        }
    } catch (e) { showToast('Hata olustu', 'error'); }
}

// ============ MODALS ============
function closeModal(id) { document.getElementById(id).classList.remove('active'); }
document.addEventListener('click', e => { if (e.target.classList.contains('modal-overlay')) e.target.classList.remove('active'); });
document.addEventListener('keydown', e => { if (e.key === 'Escape') document.querySelectorAll('.modal-overlay.active').forEach(m => m.classList.remove('active')); });

// ============ MOBILE ============
function goBack() {
    document.getElementById('sidebar').classList.remove('hidden');
    if (window.innerWidth <= 768) {
        document.getElementById('activeChat').style.display = 'none';
        document.getElementById('noChatSelected').style.display = 'flex';
    }
}

function switchMobileTab(tab) {
    document.querySelectorAll('.mobile-nav-item').forEach(b => b.classList.remove('active'));
    document.querySelector(`.mobile-nav-item[data-tab="${tab}"]`).classList.add('active');

    if (tab === 'chats') {
        document.getElementById('sidebar').classList.remove('hidden');
        closeAllModals();
    } else if (tab === 'add') {
        openAddContactModal();
    } else if (tab === 'profile') {
        openProfileModal();
    } else if (tab === 'settings') {
        document.getElementById('settingsModal').classList.add('active');
    }
}

function closeAllModals() {
    document.querySelectorAll('.modal-overlay.active').forEach(m => m.classList.remove('active'));
}

// ============ LOGOUT ============
async function handleLogout() {
    try { await fetch('/api/auth/logout', { method: 'POST' }); } catch (e) {}
    window.location.href = '/login';
}

// ============ UTILS ============
function escapeHtml(t) { const d = document.createElement('div'); d.textContent = t; return d.innerHTML; }

function formatTime(ts) {
    if (!ts) return '';
    const d = new Date(ts);
    if (isNaN(d.getTime())) return '';
    return d.toLocaleTimeString('tr-TR', { hour: '2-digit', minute: '2-digit' });
}

function formatDate(ts) {
    if (!ts) return '';
    const d = new Date(ts);
    if (isNaN(d.getTime())) return '';
    const today = new Date();
    const yest = new Date(today); yest.setDate(yest.getDate() - 1);
    if (d.toDateString() === today.toDateString()) return 'Bugun';
    if (d.toDateString() === yest.toDateString()) return 'Dun';
    return d.toLocaleDateString('tr-TR', { day: 'numeric', month: 'long', year: 'numeric' });
}

function formatDateTime(ts) {
    if (!ts) return 'Bilinmiyor';
    const d = new Date(ts);
    if (isNaN(d.getTime())) return 'Bilinmiyor';
    const today = new Date();
    if (d.toDateString() === today.toDateString())
        return 'Bugun ' + d.toLocaleTimeString('tr-TR', { hour: '2-digit', minute: '2-digit' });
    return d.toLocaleDateString('tr-TR', { day: 'numeric', month: 'short' }) + ' ' +
           d.toLocaleTimeString('tr-TR', { hour: '2-digit', minute: '2-digit' });
}

function showToast(msg, type = 'info') {
    const c = document.getElementById('toastContainer');
    const t = document.createElement('div');
    t.className = `toast ${type}`;
    t.textContent = msg;
    c.appendChild(t);
    setTimeout(() => { t.style.animation = 'toastOut .3s ease forwards'; setTimeout(() => t.remove(), 300); }, 3000);
}
