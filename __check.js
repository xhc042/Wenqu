/**
 * 问渠（Wenqu）v1.1 前端应用逻辑
 * 单页应用（SPA）主控制器
 */

// ==================== 工具函数 ====================
/**
 * 将数据库UTC时间字符串解析为本地Date对象
 * SQLite CURRENT_TIMESTAMP 存储UTC格式 "YYYY-MM-DD HH:MM:SS"，需转为ISO+Z正确解析
 */
function parseDBTime(d) {
    if (!d) return null;
    const iso = typeof d === 'string' && d.includes('T') ? d : String(d).replace(' ', 'T');
    return new Date(iso.endsWith('Z') ? iso : iso + 'Z');
}

// ==================== 状态管理 ====================
const AppState = {
    currentView: 'dashboard',
    currentCourseId: null,
    currentSessionId: null,
    ws: null,
    chatMessages: [],
    isChatting: false,
    annotationHistory: [],
    annotationSessionId: null,
    defenseQuestions: [],
    defenseAnswers: [],
    defenseTimer: null,
    roles: [],
};

// ==================== API 封装 ====================
const API = {
    async get(url) {
        const resp = await fetch(url);
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        return resp.json();
    },
    async post(url, data) {
        const resp = await fetch(url, {
            method: 'POST',
            headers: data instanceof FormData ? {} : { 'Content-Type': 'application/json' },
            body: data instanceof FormData ? data : JSON.stringify(data),
        });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        return resp.json();
    },
    async patch(url, data) {
        const resp = await fetch(url, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        return resp.json();
    },
    async delete(url) {
        const resp = await fetch(url, { method: 'DELETE' });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        return resp.json();
    },
    async upload(file) {
        const form = new FormData();
        form.append('file', file);
        return this.post('/api/courses/upload', form);
    },
};

// ==================== Toast 提示 ====================
function showToast(message, type = '') {
    let toast = document.getElementById('toast');
    if (!toast) {
        toast = document.createElement('div');
        toast.id = 'toast';
        toast.className = 'toast';
        document.body.appendChild(toast);
    }
    toast.textContent = message;
    toast.className = `toast ${type}`;
    toast.classList.add('show');
    setTimeout(() => toast.classList.remove('show'), 3000);
}

// ==================== 页面切换 ====================
function switchView(viewName) {
    AppState.currentView = viewName;
    document.querySelectorAll('.page-panel').forEach(p => p.classList.remove('active'));
    const panel = document.getElementById(`page-${viewName}`);
    if (panel) panel.classList.add('active');

    document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
    const navBtn = document.querySelector(`.nav-btn[data-view="${viewName}"]`);
    if (navBtn) navBtn.classList.add('active');
}

// ==================== 侧边栏 ====================
async function loadCourseList() {
    try {
        const courses = await API.get('/api/courses');
        const container = document.getElementById('sidebar-course-list');
        container.innerHTML = '';

        if (courses.length === 0) {
            container.innerHTML = `<div class="empty-state" style="padding:20px">
                <p style="color:#636e72;font-size:13px">还没有课程，点击"+"创建</p>
            </div>`;
            return;
        }

        courses.forEach(c => {
            const progress = c.progress || { percent: 0 };
            const card = document.createElement('div');
            card.className = `course-card ${c.id === AppState.currentCourseId ? 'active' : ''}`;
            card.innerHTML = `
                <div class="course-title">${c.title}</div>
                <div class="course-meta">${c.source_type} · 共${c.total_chapters || '?'}章</div>
                <div class="progress-mini"><div class="progress-mini-bar" style="width:${progress.percent}%"></div></div>
            `;
            card.onclick = () => openCourse(c.id);
            container.appendChild(card);
        });
    } catch (e) {
        console.error('加载课程列表失败', e);
    }
}

// ==================== 仪表盘 ====================
async function loadDashboard() {
    try {
        const courses = await API.get('/api/courses');
        const year = new Date().getFullYear();
        const heatData = await API.get(`/api/events/heatmap?year=${year}`);

        // 统计数据
        let totalDialogues = 0;
        let totalAnnotations = 0;
        let totalCompleted = 0;
        courses.forEach(c => {
            const p = c.progress || {};
            totalCompleted += p.mastered || 0;
        });

        document.getElementById('stat-courses').textContent = courses.length;
        document.getElementById('stat-completed').textContent = totalCompleted;
        document.getElementById('stat-heat-total').textContent = heatData.data.reduce((s, d) => s + d.count, 0);

        // 渲染热力图
        renderHeatmap(heatData.data, year);

        // 课程列表
        const recentList = document.getElementById('recent-courses');
        recentList.innerHTML = '';
        if (courses.length === 0) {
            recentList.innerHTML = '<div class="empty-state"><div class="empty-icon">📚</div><h3>还没有课程</h3><p>点击上方"创建课程"开始你的学习之旅</p></div>';
        } else {
            courses.slice(0, 5).forEach(c => {
                const p = c.progress || { percent: 0 };
                const div = document.createElement('div');
                div.className = 'course-card';
                div.style.background = 'var(--bg-secondary)';
                div.style.boxShadow = 'var(--shadow)';
                div.innerHTML = `
                    <div style="display:flex;justify-content:space-between;align-items:center">
                        <div class="course-title" style="color:var(--text-primary)">${c.title}</div>
                        <span style="font-size:12px;color:var(--text-secondary)">${p.percent}%</span>
                    </div>
                    <div class="progress-mini"><div class="progress-mini-bar" style="width:${p.percent}%;background:var(--accent)"></div></div>
                `;
                div.onclick = () => openCourse(c.id);
                recentList.appendChild(div);
            });
        }
    } catch (e) {
        console.error('加载仪表盘失败', e);
    }
}

function renderHeatmap(data, year) {
    const grid = document.getElementById('heatmap-grid');
    const legend = document.getElementById('heatmap-legend');
    grid.innerHTML = '';
    legend.innerHTML = '';

    // 构建日期到计数的映射
    const countMap = {};
    data.forEach(d => { countMap[d.date] = d.count; });

    // 获取该年第一天和最后一天
    const start = new Date(year, 0, 1);
    const end = new Date(year, 11, 31);

    // 计算周偏移
    const dayOfWeek = start.getDay();
    // 填充空白（第一天之前的空单元格）
    for (let i = 0; i < dayOfWeek; i++) {
        const cell = document.createElement('div');
        cell.className = 'heatmap-cell level-0';
        cell.style.background = 'transparent';
        grid.appendChild(cell);
    }

    // 遍历每一天
    const current = new Date(start);
    while (current <= end) {
        const dateStr = current.toISOString().split('T')[0];
        const count = countMap[dateStr] || 0;
        const level = count === 0 ? 0 : count <= 3 ? 1 : count <= 10 ? 2 : count <= 20 ? 3 : 4;

        const cell = document.createElement('div');
        cell.className = `heatmap-cell level-${level}`;
        cell.title = `${dateStr}: ${count} 次学习活动`;
        cell.onclick = () => loadDailySummary(dateStr);
        grid.appendChild(cell);

        current.setDate(current.getDate() + 1);
    }

    // 图例
    legend.innerHTML = `Less <span class="legend-cell" style="background:#ebedf0"></span>
        <span class="legend-cell" style="background:#9be9a8"></span>
        <span class="legend-cell" style="background:#40c463"></span>
        <span class="legend-cell" style="background:#30a14e"></span>
        <span class="legend-cell" style="background:#216e39"></span> More`;
}

async function loadDailySummary(dateStr) {
    try {
        const data = await API.get(`/api/events/daily_summary?date=${dateStr}`);
        const div = document.getElementById('daily-summary');
        div.style.display = 'block';
        const events = data.events || {};
        const courseNames = (data.courses || []).map(c => c.title).join('、');
        div.innerHTML = `<strong>${dateStr}</strong>：共 ${data.total} 次学习活动
            ${courseNames ? `（${courseNames}）` : ''}
            ${events.login ? `·登录${events.login}次` : ''}
            ${events.dialogue_round ? `·对话${events.dialogue_round}轮` : ''}
            ${events.annotation_ask ? `·划词${events.annotation_ask}次` : ''}
            ${events.lesson_end ? `·完成${events.lesson_end}节课` : ''}`;
    } catch (e) {
        console.error('加载每日摘要失败', e);
    }
}

// ==================== 创建课程 ====================
function showCreateCourse() {
    switchView('create-course');
}

function showUploadArea(mode) {
    const uploadArea = document.getElementById('upload-area');
    const textArea = document.getElementById('text-paste-area');
    const urlArea = document.getElementById('url-area');
    const recommendArea = document.getElementById('recommend-area');

    uploadArea.style.display = mode === 'upload' ? 'block' : 'none';
    textArea.style.display = mode === 'text' ? 'block' : 'none';
    urlArea.style.display = mode === 'url' ? 'block' : 'none';
    recommendArea.style.display = mode === 'recommend' ? 'block' : 'none';

    document.getElementById('upload-type').value = mode;
}

// 文件拖放/上传
function setupFileUpload() {
    const zone = document.getElementById('file-drop-zone');
    const fileInput = document.getElementById('file-input');

    zone.onclick = () => fileInput.click();
    zone.ondragover = (e) => { e.preventDefault(); zone.style.borderColor = 'var(--accent)'; };
    zone.ondragleave = () => zone.style.borderColor = '';
    zone.ondrop = (e) => {
        e.preventDefault();
        zone.style.borderColor = '';
        if (e.dataTransfer.files.length) handleFile(e.dataTransfer.files[0]);
    };
    fileInput.onchange = () => {
        if (fileInput.files.length) handleFile(fileInput.files[0]);
    };
}

async function handleFile(file) {
    try {
        const result = await API.upload(file);
        document.getElementById('file-name').textContent = `已选择: ${file.name}`;
        document.getElementById('file-name').style.display = 'block';
        document.getElementById('file-path').value = result.file_path;
        document.getElementById('file-source-type').value = result.source_type;
        showToast('文件上传成功');
    } catch (e) {
        showToast('上传失败: ' + e.message, 'error');
    }
}

async function submitCreateCourse() {
    const title = document.getElementById('course-title-input').value.trim();
    const mode = document.getElementById('upload-type').value;

    if (!title) { showToast('请输入课程名称', 'error'); return; }

    try {
        let sourceType = mode;
        let sourcePath = '';

        if (mode === 'upload') {
            sourcePath = document.getElementById('file-path').value;
            sourceType = document.getElementById('file-source-type').value || 'text';
            if (!sourcePath && document.getElementById('file-input').files.length) {
                const result = await API.upload(document.getElementById('file-input').files[0]);
                sourcePath = result.file_path;
                sourceType = result.source_type;
            }
            if (!sourcePath) { showToast('请上传文件', 'error'); return; }
        } else if (mode === 'url') {
            sourcePath = document.getElementById('url-input').value.trim();
            sourceType = 'url';
            if (!sourcePath) { showToast('请输入URL', 'error'); return; }
        } else if (mode === 'text') {
            sourcePath = document.getElementById('text-content').value.trim();
            sourceType = 'text';
            if (!sourcePath) { showToast('请粘贴文本内容', 'error'); return; }
        } else if (mode === 'recommend') {
            sourceType = 'recommendation';
        }

        const course = await API.post('/api/courses', {
            title,
            source_type: sourceType,
            source_path: sourcePath,
            content_text: mode === 'text' ? document.getElementById('text-content').value : '',
        });

        showToast('课程创建成功！', 'success');
        await loadCourseList();

        // 如果有内容，自动分章
        if (mode !== 'recommendation' && sourcePath) {
            showToast('正在智能分章...');
            try {
                await API.post(`/api/courses/${course.course_id}/chapters/generate`, {});
                await API.post(`/api/courses/${course.course_id}/syllabus/generate`, {});
                showToast('分章完成！', 'success');
            } catch (e) {
                showToast('分章失败，请稍后重试', 'error');
            }
        }

        openCourse(course.course_id);
    } catch (e) {
        showToast('创建失败: ' + e.message, 'error');
    }
}

// ==================== 打开课程 ====================
async function openCourse(courseId) {
    AppState.currentCourseId = courseId;
    switchView('course-detail');
    await loadCourseList();

    // 确保角色信息已加载（用于显示中文名）
    if (!AppState.roles || Object.keys(AppState.roles).length === 0) {
        try {
            AppState.roles = await API.get('/api/roles');
        } catch(e) {}
    }

    try {
        const data = await API.get(`/api/courses/${courseId}`);
        const course = data.course;
        if (!course) { showToast('课程不存在', 'error'); return; }

        // 更新header
        const sessionCount = data.sessions_count || 0;
        const stats = data.learning_stats || { total_minutes: 0, total_rounds: 0, total_tokens: 0 };
        const currentTeacherId = course.current_teacher;
        const currentTeacherInfo = currentTeacherId && AppState.roles ? AppState.roles[currentTeacherId] : null;
        const teacherDisplay = currentTeacherInfo
            ? `${currentTeacherInfo.emoji} ${currentTeacherInfo.name}`
            : '未设置';
        const mins = stats.total_minutes || 0;
        const hrs = Math.floor(mins / 60);
        const remainMins = mins % 60;
        const timeStr = hrs > 0 ? `${hrs}小时${remainMins}分钟` : `${remainMins}分钟`;
        const tokensStr = stats.total_tokens >= 1000
            ? (stats.total_tokens / 1000).toFixed(1) + 'k'
            : stats.total_tokens.toString();
        document.getElementById('course-header').innerHTML = `
            <div class="course-detail-header">
                <div>
                    <div class="course-title">${course.title}</div>
                    <div class="course-source">${course.source_type} · ${parseDBTime(course.created_at).toLocaleDateString()}
                        · <span style="color:var(--accent);font-weight:500">已学习 ${sessionCount} 次</span>
                    </div>
                    <div style="display:flex;gap:16px;margin-top:8px;font-size:13px;color:var(--text-secondary)">
                        <span>🧑‍🏫 教师：<strong>${teacherDisplay}</strong></span>
                        <span>⏱ 时长：<strong>${timeStr}</strong></span>
                        <span>🔤 Token：<strong>${tokensStr}</strong></span>
                    </div>
                </div>
                <div style="display:flex;gap:8px">
                    <button class="btn btn-outline btn-sm" onclick="showContractModal()">📋 学习契约</button>
                    <button class="btn btn-outline btn-sm" onclick="showSlidersModal()">🎛️ 风格调控</button>
                    <button class="btn btn-outline btn-sm" onclick="showSettingsModal()">⚙️ 设置</button>
                    <button class="btn btn-danger btn-sm" onclick="deleteCurrentCourse()">🗑️</button>
                </div>
            </div>
        `;

        // 进度条
        const progress = data.progress || { total: 0, mastered: 0, percent: 0 };
        document.getElementById('course-progress').innerHTML = `
            <div class="progress-bar-container">
                <div style="display:flex;justify-content:space-between;margin-bottom:8px">
                    <span style="font-weight:500">课程进度</span>
                    <span>${progress.mastered}/${progress.total} 项已掌握 (${progress.percent}%)</span>
                </div>
                <div class="progress-bar-outer">
                    <div class="progress-bar-inner" style="width:${progress.percent}%"></div>
                </div>
            </div>
        `;

        // 结业答辩按钮
        const defenseBanner = document.getElementById('defense-banner');
        if (progress.total > 0 && progress.percent === 100) {
            defenseBanner.classList.add('show');
            defenseBanner.innerHTML = `<h2>🎓 恭喜！所有掌握项已完成</h2>
                <p>准备好接受结业答辩了吗？</p>
                <button class="btn btn-primary" onclick="startDefense()" style="margin-top:12px">🎓 申请结业答辩</button>`;
        } else {
            defenseBanner.classList.remove('show');
        }

        // 章节列表
        renderChapters(data.chapters || [], data.syllabus || []);

        // 课后产出物
        renderPostClass(data);

        // 证书
        renderCertificates(data.certificates || []);

        // 推荐角色
        loadRoleRecommendation(courseId);

    } catch (e) {
        showToast('加载课程失败', 'error');
    }
}

function renderChapters(chapters, syllabus) {
    const list = document.getElementById('chapter-list');
    list.innerHTML = '';

    if (chapters.length === 0) {
        list.innerHTML = '<div class="empty-state"><div class="empty-icon">📖</div><h3>暂无章节</h3><p>请先完成分章处理</p></div>';
        return;
    }

    chapters.forEach((ch, idx) => {
        const chapterSyllabus = syllabus.filter(s => s.chapter_index === ch.idx);
        const mastered = chapterSyllabus.filter(s => s.status === 'mastered').length;
        const total = chapterSyllabus.length;

        const div = document.createElement('div');
        div.className = 'chapter-item';
        div.innerHTML = `
            <div class="chapter-index">${ch.idx + 1}</div>
            <div class="chapter-info">
                <div class="chapter-title">${ch.title}</div>
                <div class="chapter-items-count">
                    ${total > 0 ? `${mastered}/${total} 项掌握` : '暂无掌握项'}
                    <span style="margin-left:8px">
                        ${chapterSyllabus.map(s => `<span class="tag tag-${s.status === 'mastered' ? 'mastered' : s.status === 'in_progress' ? 'progress' : 'pending'}" style="margin:0 2px">${s.description.slice(0, 20)}...</span>`).join(' ')}
                    </span>
                </div>
            </div>
            <button class="btn btn-primary btn-sm" onclick="startChat(${ch.idx})">开始学习</button>
        `;
        list.appendChild(div);
    });
}

// ==================== 学习契约弹窗 ====================
function showContractModal() {
    const modal = document.getElementById('contract-modal');
    modal.classList.add('active');

    // 加载角色
    loadRolesForContract();
}

function renderRoleCard(role, roleId, recommended, currentTeacherId) {
    const isRec = recommended && recommended.includes(roleId);
    const isCurrent = currentTeacherId && roleId === currentTeacherId;
    const selectedClass = isCurrent ? 'selected' : (isRec ? 'selected' : '');
    return `<div class="role-card-wide ${selectedClass}" data-role-id="${roleId}">
        ${isRec && !isCurrent ? '<div class="recommend-badge">⭐ 推荐</div>' : ''}
        ${isCurrent ? '<div class="recommend-badge" style="background:var(--accent)">✅ 当前教师</div>' : ''}
        <div class="role-header">
            <div class="role-emoji">${role.emoji || '🎓'}</div>
            <div class="role-name-group">
                <div class="role-name">${role.name || roleId}</div>
                <div class="role-style">${role.style || ''}</div>
            </div>
        </div>
        <div class="role-personality">${role.personality || ''}</div>
        <div class="role-best-for">📌 ${role.best_for || ''}</div>
        <div class="role-tags">
            ${(role.tags || []).map(t => `<span class="role-tag">${t}</span>`).join('')}
        </div>
        <div class="role-actions">
            <button class="btn btn-primary btn-sm select-role-btn" data-role-id="${roleId}">选择此教师</button>
            <button class="btn btn-outline btn-sm view-role-btn" data-role-id="${roleId}">查看详情</button>
        </div>
    </div>`;
}

async function loadRolesForContract() {
    try {
        const roles = await API.get('/api/roles');
        AppState.roles = roles;
        const grid = document.getElementById('contract-role-grid');
        grid.innerHTML = '';
        // 获取当前教师和推荐
        let recommended = [];
        let currentTeacherId = null;
        if (AppState.currentCourseId) {
            try {
                const courseData = await API.get(`/api/courses/${AppState.currentCourseId}`);
                const course = courseData.course || {};
                currentTeacherId = course.current_teacher || null;
                const rec = await API.get(`/api/courses/${AppState.currentCourseId}/roles/recommend`);
                recommended = rec.recommended || [];
            } catch(e) {}
        }
        // 排序：当前教师 > 推荐 > 其他
        const entries = Object.entries(roles);
        entries.sort(([a], [b]) => {
            const aCur = a === currentTeacherId ? -1 : 0;
            const bCur = b === currentTeacherId ? -1 : 0;
            const aRec = recommended.includes(a) ? 0 : 1;
            const bRec = recommended.includes(b) ? 0 : 1;
            return (aCur - bCur) || (aRec - bRec);
        });
        grid.innerHTML = entries.map(([id, role]) => renderRoleCard(role, id, recommended, currentTeacherId)).join('');
        // 绑定事件
        grid.querySelectorAll('.select-role-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                const roleId = btn.dataset.roleId;
                grid.querySelectorAll('.role-card-wide').forEach(c => c.classList.remove('selected'));
                grid.querySelector(`.role-card-wide[data-role-id="${roleId}"]`).classList.add('selected');
            });
        });
        grid.querySelectorAll('.view-role-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                const roleId = btn.dataset.roleId;
                viewRoleDetail(roleId);
            });
        });
        // 卡片本身点击 = 选中
        grid.querySelectorAll('.role-card-wide').forEach(card => {
            card.addEventListener('click', (e) => {
                if (e.target.closest('button')) return;
                grid.querySelectorAll('.role-card-wide').forEach(c => c.classList.remove('selected'));
                card.classList.add('selected');
            });
        });
    } catch (e) {
        console.error('加载角色失败', e);
    }
}

async function viewRoleDetail(roleId) {
    const modal = document.getElementById('role-detail-modal');
    const content = document.getElementById('role-detail-content');
    content.innerHTML = '<div style="text-align:center;padding:20px;color:#636e72">加载中...</div>';
    modal.classList.add('active');

    try {
        const role = await API.get(`/api/roles/${roleId}`);
        content.innerHTML = `
            <div class="role-detail-header">
                <div class="role-emoji">${role.emoji || '🎓'}</div>
                <div>
                    <div class="role-name">${role.name}</div>
                    <div class="role-style">${role.style || ''}</div>
                </div>
                <button class="btn btn-outline btn-sm" onclick="closeRoleDetailModal()" style="margin-left:auto">✕</button>
            </div>
            <div class="role-detail-section">
                <h4>🧑‍🏫 性格标签</h4>
                <p>${role.personality || '无'}</p>
            </div>
            <div class="role-detail-section">
                <h4>🎯 最佳场景</h4>
                <p>${role.best_for || '无'}</p>
            </div>
            <div class="role-detail-section">
                <h4>🏷️ 标签</h4>
                <div class="role-tags">${(role.tags || []).map(t => `<span class="role-tag">${t}</span>`).join('')}</div>
            </div>
            <div class="role-detail-section">
                <h4>📜 完整提示词</h4>
                <div class="prompt-box">${escapeHtml(role.prompt || '暂无提示词')}</div>
            </div>
        `;
    } catch (e) {
        content.innerHTML = `<div style="text-align:center;padding:20px;color:var(--danger)">加载失败: ${e.message}</div>`;
    }
}

function closeRoleDetailModal() {
    document.getElementById('role-detail-modal').classList.remove('active');
}

function selectDepth(depth) {
    document.querySelectorAll('.depth-btn').forEach(b => b.classList.remove('selected'));
    document.querySelector(`.depth-btn[data-depth="${depth}"]`).classList.add('selected');
}

function selectDuration(duration) {
    document.querySelectorAll('.duration-btn').forEach(b => b.classList.remove('selected'));
    document.querySelector(`.duration-btn[data-duration="${duration}"]`).classList.add('selected');
}

async function confirmContract() {
    const selectedRole = document.querySelector('#contract-role-grid .role-card-wide.selected');
    const selectedDepth = document.querySelector('.depth-btn.selected');
    const selectedDuration = document.querySelector('.duration-btn.selected');

    if (!selectedRole) { showToast('请选择一位教师', 'error'); return; }
    if (!selectedDepth) { showToast('请选择认知深度', 'error'); return; }
    if (!selectedDuration) { showToast('请选择学习时长', 'error'); return; }

    try {
        await API.patch(`/api/courses/${AppState.currentCourseId}/contract`, {
            teacher: selectedRole.dataset.roleId,
            depth: selectedDepth.dataset.depth,
            duration: parseInt(selectedDuration.dataset.duration),
        });
        showToast('学习契约已保存！', 'success');
        document.getElementById('contract-modal').classList.remove('active');
    } catch (e) {
        showToast('保存失败', 'error');
    }
}

function closeContractModal() {
    document.getElementById('contract-modal').classList.remove('active');
}

// ==================== 风格调控弹窗 ====================
function showSlidersModal() {
    const modal = document.getElementById('sliders-modal');
    modal.classList.add('active');

    // 加载当前角色的滑块值
    if (AppState.currentCourseId) {
        // 从角色列表中选第一个或当前教师
        const roleSelect = document.getElementById('slider-role-select');
        if (roleSelect.options.length === 0) {
            Object.entries(AppState.roles).forEach(([id, role]) => {
                const opt = document.createElement('option');
                opt.value = id;
                opt.textContent = role.emoji + ' ' + role.name;
                roleSelect.appendChild(opt);
            });
        }
        loadSlidersForRole(roleSelect.value);
    }
}

async function loadSlidersForRole(roleId) {
    try {
        const sliders = await API.get(`/api/courses/${AppState.currentCourseId}/sliders/${roleId}`);
        document.getElementById('slider-strictness').value = sliders.strictness || 0;
        document.getElementById('slider-encouragement').value = sliders.encouragement || 0;
        document.getElementById('slider-verbosity').value = sliders.verbosity || 0;
        updateSliderLabels();
    } catch (e) {
        console.error('加载滑块失败', e);
    }
}

function updateSliderLabels() {
    document.getElementById('strictness-val').textContent = document.getElementById('slider-strictness').value;
    document.getElementById('encouragement-val').textContent = document.getElementById('slider-encouragement').value;
    document.getElementById('verbosity-val').textContent = document.getElementById('slider-verbosity').value;
}

async function saveSliders() {
    const roleId = document.getElementById('slider-role-select').value;
    try {
        await API.post(`/api/courses/${AppState.currentCourseId}/sliders/${roleId}`, {
            strictness: parseInt(document.getElementById('slider-strictness').value),
            encouragement: parseInt(document.getElementById('slider-encouragement').value),
            verbosity: parseInt(document.getElementById('slider-verbosity').value),
        });
        showToast('风格设置已保存！', 'success');
    } catch (e) {
        showToast('保存失败', 'error');
    }
}

function closeSlidersModal() {
    document.getElementById('sliders-modal').classList.remove('active');
}

// ==================== 对话 ====================
async function startChat(chapterIndex) {
    const courseId = AppState.currentCourseId;
    const course = await API.get(`/api/courses/${courseId}`);
    const teacherId = course.course.current_teacher || 'ganyu';
    const depth = course.course.current_depth || 'standard';
    AppState.currentTeacherId = teacherId;

    // 确保角色信息已加载
    if (!AppState.roles || Object.keys(AppState.roles).length === 0) {
        AppState.roles = await API.get('/api/roles');
    }

    try {
        const result = await API.post(`/api/courses/${courseId}/chat/start`, {
            chapter_index: chapterIndex,
            teacher_role_id: teacherId,
            depth: depth,
        });
        AppState.currentSessionId = result.session_id;

        switchView('chat');
        document.getElementById('chat-messages').innerHTML = '';
        document.getElementById('chat-status').textContent = '正在建立连接...';
        document.getElementById('chat-input').disabled = true;
        document.getElementById('send-btn').disabled = true;

        connectWebSocket(result.session_id);
    } catch (e) {
        showToast('启动对话失败: ' + e.message, 'error');
    }
}

function connectWebSocket(sessionId) {
    if (AppState.ws) {
        AppState.ws.close();
    }

    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${location.host}/ws/chat/${sessionId}`;
    const ws = new WebSocket(wsUrl);

    ws.onopen = () => {
        document.getElementById('chat-status').textContent = '已连接';
        AppState.isChatting = true;
    };

    ws.onmessage = (event) => {
        const msg = JSON.parse(event.data);

        if (msg.error) {
            showToast(msg.error, 'error');
            return;
        }

        if (msg.state === 'SHARE' || msg.state === 'PROBE') {
            appendStreamContent(msg.state, msg.content);
        } else if (msg.state === 'SHARE_DONE' || msg.state === 'PROBE_DONE' || msg.state === 'TURN_DONE') {
            // 状态完成，不做特殊处理
        } else if (msg.state === 'WAIT_USER') {
            document.getElementById('chat-input').disabled = false;
            document.getElementById('send-btn').disabled = false;
            document.getElementById('chat-status').textContent = '等待你的回答...';
            document.getElementById('chat-input').focus();
        } else if (msg.state === 'EXPLAIN' || msg.state === 'GUIDE') {
            appendStreamContent(msg.state, msg.content);
        } else if (msg.state === 'END') {
            // END消息内容显示
        } else if (msg.state === 'SESSION_END') {
            document.getElementById('chat-status').textContent = '课程结束';
            document.getElementById('chat-input').disabled = true;
            document.getElementById('send-btn').disabled = true;
            AppState.isChatting = false;
            showToast('本节课学习结束！正在生成学习记录...', 'success');
            // 等待课后闭环完成后刷新数据
            setTimeout(async () => {
                await openCourse(AppState.currentCourseId);
                // 切换到课后产出物标签，显示最新内容
                showToast('学习记录已生成！', 'success');
            }, 3000);
        }
    };

    ws.onerror = () => {
        showToast('WebSocket连接失败', 'error');
        document.getElementById('chat-status').textContent = '连接失败';
    };

    ws.onclose = () => {
        if (AppState.isChatting) {
            showToast('连接已断开', 'error');
        }
        AppState.isChatting = false;
    };

    AppState.ws = ws;
}

function getTeacherInfo() {
    const teacherId = AppState.currentTeacherId || 'ganyu';
    const role = AppState.roles ? AppState.roles[teacherId] : null;
    return {
        id: teacherId,
        name: role ? role.name : '教师',
        emoji: role ? role.emoji : '🎓',
    };
}

function appendStreamContent(state, content) {
    const container = document.getElementById('chat-messages');
    let lastMsg = container.lastElementChild;
    const teacher = getTeacherInfo();

    if (!lastMsg || lastMsg.dataset.state !== state || lastMsg.dataset.finalized === 'true') {
        const div = document.createElement('div');
        div.className = 'message';
        div.dataset.state = state;
        div.dataset.finalized = 'false';

        const stateLabels = {
            'SHARE': '分享',
            'PROBE': '提问',
            'EXPLAIN': '讲解',
            'GUIDE': '引导',
            'END': '结束',
        };
        const label = stateLabels[state] || '';
        const roleName = label ? `${teacher.name} ${label}` : teacher.name;

        div.innerHTML = `
            <div class="avatar assistant">${teacher.emoji}</div>
            <div class="bubble assistant">
                <div class="state-badge">${roleName}</div>
                <div class="msg-content"></div>
            </div>
        `;
        container.appendChild(div);
        lastMsg = div;
    }

    const contentDiv = lastMsg.querySelector('.msg-content');
    contentDiv.textContent += content;
    container.scrollTop = container.scrollHeight;
}

function sendChatMessage() {
    const input = document.getElementById('chat-input');
    const text = input.value.trim();

    if (!text || !AppState.ws || AppState.ws.readyState !== WebSocket.OPEN) return;

    // 添加用户消息到界面
    const container = document.getElementById('chat-messages');
    const userDiv = document.createElement('div');
    userDiv.className = 'message';
    userDiv.innerHTML = `
        <div class="avatar user">👤</div>
        <div class="bubble user">${escapeHtml(text)}</div>
    `;
    container.appendChild(userDiv);
    container.scrollTop = container.scrollHeight;

    // 标记上一条AI消息为已完成
    const lastMsg = container.lastElementChild.previousElementSibling;
    if (lastMsg) lastMsg.dataset.finalized = 'true';

    // 发送到WebSocket
    AppState.ws.send(JSON.stringify({ content: text }));

    input.value = '';
    input.disabled = true;
    document.getElementById('send-btn').disabled = true;
    document.getElementById('chat-status').textContent = 'AI正在思考...';
}

function endChat() {
    if (AppState.ws && AppState.ws.readyState === WebSocket.OPEN) {
        AppState.ws.send(JSON.stringify({ action: 'end' }));
    }
    showToast('对话已结束');
}

function escapeHtml(text) {
    if (!text) return text;
    // 移除思考标签，兼容带思考模式的模型
    text = text.replace(/<think>[\s\S]*?<\/think>/gi, '');
    text = text.replace(/<thinking>[\s\S]*?<\/thinking>/gi, '');
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// ==================== 划词问答 ====================
function setupAnnotationSystem() {
    document.addEventListener('mouseup', (e) => {
        const selection = window.getSelection();
        if (!selection || selection.toString().length <= 3) return;

        const container = document.getElementById('chat-messages');
        if (!container.contains(selection.anchorNode)) return;

        // 检查是否选中了AI消息
        const msgBubble = selection.anchorNode.closest ? selection.anchorNode.closest('.bubble.assistant') : null;
        if (!msgBubble) return;

        // 显示浮动按钮
        removeFloatingBtn();
        const btn = document.createElement('div');
        btn.className = 'floating-annotation-btn';
        btn.textContent = '💬 追问';
        btn.style.cssText = `
            position: fixed; left: ${e.pageX}px; top: ${e.pageY - 40}px;
            background: var(--accent); color: white; border: none;
            border-radius: 20px; padding: 6px 14px; font-size: 13px;
            cursor: pointer; z-index: 100; box-shadow: 0 2px 8px rgba(0,0,0,0.2);
            transition: all 0.2s;
        `;
        btn.onclick = () => {
            const quoted = selection.toString().trim();
            openAnnotationDrawer(quoted);
            removeFloatingBtn();
        };
        document.body.appendChild(btn);
    });

    document.addEventListener('mousedown', (e) => {
        if (!e.target.classList.contains('floating-annotation-btn')) {
            removeFloatingBtn();
        }
    });
}

function removeFloatingBtn() {
    document.querySelectorAll('.floating-annotation-btn').forEach(el => el.remove());
}

function openAnnotationDrawer(quotedText) {
    AppState.annotationSessionId = Date.now().toString();
    const drawer = document.getElementById('annotation-drawer');
    drawer.classList.add('open');
    document.getElementById('drawer-quote').textContent = `"${quotedText}"`;
    document.getElementById('drawer-quote').dataset.quoted = quotedText;
    document.getElementById('annotation-input').value = '';
    document.getElementById('annotation-history').innerHTML = '';
    document.getElementById('annotation-input').focus();
}

function closeAnnotationDrawer() {
    document.getElementById('annotation-drawer').classList.remove('open');
}

async function sendAnnotation() {
    const input = document.getElementById('annotation-input');
    const question = input.value.trim();
    const quotedText = document.getElementById('drawer-quote').dataset.quoted;

    if (!question) { showToast('请输入问题', 'error'); return; }

    try {
        const result = await API.post('/api/annotate', {
            course_id: AppState.currentCourseId,
            session_id: AppState.currentSessionId || '',
            quoted_text: quotedText,
            question: question,
        });

        // 显示问答记录
        const history = document.getElementById('annotation-history');
        const div = document.createElement('div');
        div.style.cssText = 'margin-bottom:12px;padding:8px;background:#f8f9fa;border-radius:8px';
        div.innerHTML = `
            <div style="font-size:12px;color:#636e72;margin-bottom:4px">❓ ${escapeHtml(question)}</div>
            <div style="font-size:13px">💡 ${escapeHtml(result.answer)}</div>
        `;
        history.appendChild(div);
        input.value = '';
        showToast('已回答', 'success');
    } catch (e) {
        showToast('提问失败', 'error');
    }
}

// ==================== 课后产出物 ====================
function renderPostClass(data) {
    const diaries = data.diaries || [];
    const summaries = data.summaries || [];
    const groupChats = data.group_chats || [];
    const profile = data.profile || {};
    const affinities = data.affinities || [];

    // 画像
    const profileDiv = document.getElementById('post-profile');
    if (profile) {
        profileDiv.innerHTML = `
            <div style="margin-bottom:8px"><strong>💪 强项：</strong> ${(profile.strengths || []).join('、') || '暂无'}</div>
            <div style="margin-bottom:8px"><strong>📉 弱项：</strong> ${(profile.weaknesses || []).join('、') || '暂无'}</div>
            <div style="margin-bottom:8px"><strong>❌ 误解：</strong> ${(profile.misunderstandings || []).join('、') || '暂无'}</div>
        `;
    }

    // 情感分
    const affDiv = document.getElementById('post-affinities');
    if (affinities.length > 0) {
        const roles = AppState.roles;
        affDiv.innerHTML = affinities.map(a => {
            const role = roles[a.teacher_role_id] || { emoji: '🎓', name: a.teacher_role_id };
            const score = a.score;
            const hearts = score >= 80 ? '❤️❤️❤️' : score >= 60 ? '❤️❤️' : score >= 40 ? '❤️' : '🤍';
            return `<div style="margin:4px 0"><span style="font-size:16px">${role.emoji || ''}</span> ${role.name || a.teacher_role_id}: ${hearts} (${score})</div>`;
        }).join('');
    }

    function fmtTime(dateStr) {
        const d = parseDBTime(dateStr);
        return d.toLocaleString('zh-CN', {
            year: 'numeric', month: '2-digit', day: '2-digit',
            hour: '2-digit', minute: '2-digit',
        });
    }

    // 日记
    const diaryDiv = document.getElementById('post-diaries');
    if (diaries.length > 0) {
        diaryDiv.innerHTML = diaries.slice(0, 10).map(d =>
            `<div class="fade-in" style="background:var(--bg-primary);padding:12px;border-radius:8px;margin-bottom:8px">
                <div style="display:flex;justify-content:space-between;align-items:flex-start">
                    <div style="font-weight:500;margin-bottom:4px">📝 ${d.title}</div>
                    <div style="font-size:11px;color:#b2bec3;white-space:nowrap">${fmtTime(d.created_at)}</div>
                </div>
                <div style="font-size:13px;color:#636e72">${escapeHtml(d.content).slice(0, 300)}</div>
            </div>`
        ).join('');
    } else {
        diaryDiv.innerHTML = '<div style="color:#636e72;font-size:13px;padding:12px;text-align:center">暂无日记</div>';
    }

    // 群聊
    const chatDiv = document.getElementById('post-group-chats');
    if (groupChats.length > 0) {
        const roles = AppState.roles;
        chatDiv.innerHTML = groupChats.map(g => {
            const role = roles[g.teacher_role_id] || { emoji: '🎓', name: g.teacher_role_id };
            return `<div class="fade-in" style="background:var(--bg-primary);padding:12px;border-radius:8px;margin-bottom:8px">
                <div style="display:flex;justify-content:space-between;align-items:flex-start">
                    <div style="font-weight:500;margin-bottom:4px">${role.emoji} ${role.name}</div>
                    <div style="font-size:11px;color:#b2bec3;white-space:nowrap">${fmtTime(g.created_at)}</div>
                </div>
                <div style="font-size:13px">${escapeHtml(g.message)}</div>
                ${g.quoted_user_text ? `<div style="font-size:11px;color:#636e72;margin-top:4px;border-left:2px solid var(--warning);padding-left:8px">引用: "${escapeHtml(g.quoted_user_text.slice(0, 60))}"</div>` : ''}
            </div>`;
        }).join('');
    } else {
        chatDiv.innerHTML = '<div style="color:#636e72;font-size:13px;padding:12px;text-align:center">暂无群聊记录</div>';
    }

    // 总结
    const sumDiv = document.getElementById('post-summaries');
    if (summaries.length > 0) {
        sumDiv.innerHTML = summaries.slice(0, 5).map(s =>
            `<div class="fade-in" style="background:var(--bg-primary);padding:12px;border-radius:8px;margin-bottom:8px">
                <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:4px">
                    <div style="font-size:13px;font-weight:500">📋 复习总结</div>
                    <div style="font-size:11px;color:#b2bec3;white-space:nowrap">${fmtTime(s.created_at)}</div>
                </div>
                <div style="font-size:13px;white-space:pre-wrap">${escapeHtml(s.content).slice(0, 500)}</div>
            </div>`
        ).join('');
    } else {
        sumDiv.innerHTML = '<div style="color:#636e72;font-size:13px;padding:12px;text-align:center">暂无复习总结</div>';
    }
}

// 切换课后产出物标签
function switchPostTab(tab) {
    document.querySelectorAll('.post-class-tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.post-class-content').forEach(c => c.classList.remove('active'));
    document.querySelector(`.post-class-tab[data-tab="${tab}"]`).classList.add('active');
    document.getElementById(`post-${tab}`).classList.add('active');
    if (tab === 'history') {
        loadLearningHistory();
    }
}

async function loadLearningHistory() {
    const cid = AppState.currentCourseId;
    if (!cid) return;
    try {
        const [cd, data] = await Promise.all([
            API.get(`/api/courses/${cid}`),
            API.get(`/api/courses/${cid}/history`),
        ]);
        const container = document.getElementById('post-history');
        const history = data.history || [];
        const defenseRecords = data.defense_records || [];

        if (history.length === 0 && defenseRecords.length === 0) {
            container.innerHTML = '<div style="color:#636e72;font-size:13px;padding:20px;text-align:center">暂无学习记录，完成一次对话后自动生成</div>';
            return;
        }

        // 汇总统计
        const stats = cd.learning_stats || {};
        const totalSessions = history.length;
        const totalMsg = history.reduce((s, h) => s + h.message_count, 0);
        const totalRounds = history.reduce((s, h) => s + (h.session.total_rounds || 0), 0);
        const totalMinutes = stats.total_minutes || 0;
        const totalTokens = stats.total_tokens || 0;
        const hrs = Math.floor(totalMinutes / 60);
        const timeStr = hrs > 0 ? `${hrs}小时${totalMinutes % 60}分钟` : `${totalMinutes}分钟`;
        const tokensStr = totalTokens >= 1000 ? (totalTokens / 1000).toFixed(1) + 'k' : totalTokens.toString();
        const masteredCount = cd.progress ? cd.progress.mastered : 0;
        const totalCount = cd.progress ? cd.progress.total : 0;

        function fmtTime(d) { const dt = parseDBTime(d); return dt ? dt.toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}) : ''; }

        let html = `
        <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:12px;margin-bottom:20px">
            <div style="background:var(--bg-secondary);border-radius:var(--radius-sm);padding:14px;text-align:center;box-shadow:var(--shadow)">
                <div style="font-size:24px;font-weight:700;color:var(--accent)">${totalSessions}</div>
                <div style="font-size:12px;color:#636e72">对话次数</div>
            </div>
            <div style="background:var(--bg-secondary);border-radius:var(--radius-sm);padding:14px;text-align:center;box-shadow:var(--shadow)">
                <div style="font-size:24px;font-weight:700;color:var(--accent)">${timeStr}</div>
                <div style="font-size:12px;color:#636e72">学习时长</div>
            </div>
            <div style="background:var(--bg-secondary);border-radius:var(--radius-sm);padding:14px;text-align:center;box-shadow:var(--shadow)">
                <div style="font-size:24px;font-weight:700;color:var(--accent)">${totalRounds}</div>
                <div style="font-size:12px;color:#636e72">对话轮次</div>
            </div>
            <div style="background:var(--bg-secondary);border-radius:var(--radius-sm);padding:14px;text-align:center;box-shadow:var(--shadow)">
                <div style="font-size:24px;font-weight:700;color:var(--accent)">${tokensStr}</div>
                <div style="font-size:12px;color:#636e72">Token消耗</div>
            </div>
            <div style="background:var(--bg-secondary);border-radius:var(--radius-sm);padding:14px;text-align:center;box-shadow:var(--shadow)">
                <div style="font-size:24px;font-weight:700;color:var(--success)">${masteredCount}/${totalCount}</div>
                <div style="font-size:12px;color:#636e72">掌握进度</div>
            </div>
        </div>`;

        // 答辩记录部分
        if (defenseRecords.length > 0) {
            html += `<div style="font-size:14px;font-weight:600;margin-bottom:12px;margin-top:20px">🎓 结业答辩</div>`;
            defenseRecords.forEach((dr, dri) => {
                const qs = dr.questions || [];
                const passCount = qs.filter(q => q.verdict === 'PASS').length;
                const failCount = qs.filter(q => q.verdict === 'FAIL').length;
                const allPassed = dr.passed;
                html += `
                <div class="fade-in" style="background:var(--bg-secondary);border-radius:var(--radius-sm);padding:16px;margin-bottom:16px;border-left:3px solid ${allPassed ? 'var(--success)' : 'var(--danger)'};box-shadow:var(--shadow)">
                    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
                        <span style="font-weight:600">${allPassed ? '🎉 答辩通过' : '😅 答辩未通过'}</span>
                        <span style="font-size:12px;color:#636e72">${parseDBTime(dr.created_at).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'})} · ${qs.length}题 ${allPassed ? '✅全过' : `✅${passCount} ❌${failCount}`}</span>
                    </div>
                    ${qs.map((q, qi) => {
                        const passed = q.verdict === 'PASS';
                        return `
                        <div style="background:var(--bg-primary);border-radius:8px;padding:12px;margin-bottom:8px;border-left:3px solid ${passed ? 'var(--success)' : 'var(--danger)'}">
                            <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:4px">
                                <div style="font-weight:500;font-size:13px">第${qi+1}题</div>
                                <span style="font-size:12px;padding:1px 8px;border-radius:4px;background:${passed ? 'rgba(0,184,148,0.1)' : 'rgba(225,112,85,0.1)'};color:${passed ? 'var(--success)' : 'var(--danger)'}">${passed ? 'PASS' : 'FAIL'}</span>
                            </div>
                            <div style="font-size:13px;margin-bottom:6px">${q.question || ''}</div>
                            <div style="font-size:12px;color:#636e72;margin-bottom:4px">💬 你的回答：${q.answer || '（未作答）'}</div>
                            ${q.comment ? `<div style="font-size:12px;color:#636e72">📝 评语：${q.comment}</div>` : ''}
                            ${!passed && q.reference_answer ? `<div style="font-size:12px;color:var(--accent);margin-top:4px;padding:6px 8px;background:rgba(108,92,231,0.06);border-radius:4px">💡 参考答案：${q.reference_answer}</div>` : ''}
                        </div>`;
                    }).join('')}
                </div>`;
            });
        }

        html += `<div style="font-size:14px;font-weight:600;margin-bottom:12px;margin-top:20px">📖 对话记录</div>`;

        html += history.map((h, idx) => {
            const s = h.session;
            const date = s.ended_at ? fmtTime(s.ended_at) : (s.started_at ? fmtTime(s.started_at) : '未知');
            const teacherName = AppState.roles[s.teacher_role_id]?.name || s.teacher_role_id;
            const teacherEmoji = AppState.roles[s.teacher_role_id]?.emoji || '🎓';
            const chapterTitle = h.chapter_title || `第${s.chapter_index + 1}章`;
            const order = history.length - idx;

            // 产出物
            let outputsHtml = '';
            if (h.diaries && h.diaries.length > 0) {
                outputsHtml += h.diaries.map(d => `<div style="margin:4px 0;font-size:12px">📝 <strong>${d.title}</strong> <span style="color:#b2bec3">${fmtTime(d.created_at)}</span></div>`).join('');
            }
            if (h.group_chats && h.group_chats.length > 0) {
                outputsHtml += h.group_chats.map(g => {
                    const tName = AppState.roles[g.teacher_role_id]?.name || g.teacher_role_id;
                    return `<div style="margin:4px 0;font-size:12px">💬 ${tName}: ${g.message.slice(0, 60)}... <span style="color:#b2bec3">${fmtTime(g.created_at)}</span></div>`;
                }).join('');
            }
            if (h.summaries && h.summaries.length > 0) {
                outputsHtml += `<div style="margin:4px 0;font-size:12px">📋 复习总结 <span style="color:#b2bec3">${fmtTime(h.summaries[0].created_at)}</span></div>`;
            }
            if (!outputsHtml) {
                outputsHtml = '<div style="color:#b2bec3;font-size:12px">课后产出物生成中...</div>';
            }

            return `<div class="fade-in" style="background:var(--bg-secondary);border-radius:var(--radius-sm);padding:16px;margin-bottom:12px;border-left:3px solid var(--accent);box-shadow:var(--shadow)">
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
                    <div>
                        <span style="font-size:12px;color:#b2bec3;font-weight:500">第${order}次</span>
                        <span style="font-weight:600;margin-left:8px">${teacherEmoji} ${teacherName}</span>
                    </div>
                    <span style="font-size:12px;color:#636e72">${date} · ${h.message_count}条消息</span>
                </div>
                <div style="font-size:13px;margin-bottom:6px">
                    <span style="background:rgba(108,92,231,0.08);color:var(--accent);padding:2px 8px;border-radius:4px;font-size:12px">📖 ${chapterTitle}</span>
                </div>
                <div style="font-size:13px;margin-bottom:8px;color:var(--text-secondary)">
                    ${h.user_messages && h.user_messages.length > 0
                        ? h.user_messages.map(m => `"${m}"`).join(' → ')
                        : ''}
                </div>
                <div style="border-top:1px solid var(--border);padding-top:8px;margin-top:8px">
                    <div style="font-size:12px;font-weight:500;color:#636e72;margin-bottom:4px">课后产出：</div>
                    ${outputsHtml}
                </div>
                <div style="margin-top:10px;text-align:right">
                    <button class="btn btn-outline btn-sm" onclick="event.stopPropagation();viewSessionHistory('${s.id}')">💬 查看完整对话</button>
                </div>
            </div>`;
        }).join('');

        container.innerHTML = html;
    } catch (e) {
        console.error('加载学习历史失败', e);
    }
}

// ==================== 查看历史对话完整内容 ====================
async function viewSessionHistory(sessionId) {
    try {
        const data = await API.get(`/api/sessions/${sessionId}`);
        const session = data.session;
        const messages = data.messages || [];
        const outputs = data.outputs || {};

        // 构建模态框内容
        const modal = document.getElementById('history-chat-modal');
        const content = document.getElementById('history-chat-content');

        // 会话头部信息
        let headerHtml = `
            <div class="history-chat-header">
                <div style="display:flex;align-items:center;gap:12px;margin-bottom:8px">
                    <span style="font-size:32px">${session.teacher_info.emoji}</span>
                    <div>
                        <div style="font-size:18px;font-weight:700">${session.teacher_info.name}</div>
                        <div style="font-size:13px;color:var(--text-secondary)">${session.teacher_info.style || ''}</div>
                    </div>
                </div>
                <div style="display:flex;flex-wrap:wrap;gap:8px;font-size:13px;color:var(--text-secondary)">
                    <span>📖 ${session.course_title}</span>
                    <span>· 第${session.chapter_index + 1}章 ${session.chapter_title}</span>
                    <span>· 💬 ${session.total_rounds || 0} 轮对话</span>
                    <span>· 📅 ${session.started_at ? parseDBTime(session.started_at).toLocaleString('zh-CN') : '未知'}</span>
                </div>
            </div>`;

        // 对话消息
        let messagesHtml = '';
        if (messages.length === 0) {
            messagesHtml = '<div class="empty-state" style="padding:40px"><p>暂无对话消息</p></div>';
        } else {
            messagesHtml = '<div class="history-chat-messages">';
            messages.forEach(m => {
                const isUser = m.role === 'user';
                const stateLabel = m.state_marker ? getStateLabel(m.state_marker) : '';
                const avatar = isUser ? '👤' : session.teacher_info.emoji;
                const time = m.created_at ? parseDBTime(m.created_at).toLocaleTimeString('zh-CN', {hour:'2-digit',minute:'2-digit'}) : '';

                messagesHtml += `
                    <div class="history-message ${isUser ? 'history-message-user' : 'history-message-assistant'}">
                        <div class="history-message-avatar">${avatar}</div>
                        <div class="history-message-bubble ${isUser ? 'user' : 'assistant'}">
                            ${!isUser && stateLabel ? `<div class="history-state-badge">${stateLabel}</div>` : ''}
                            <div class="history-message-text">${escapeHtml(m.content)}</div>
                            ${time ? `<div class="history-message-time">${time}</div>` : ''}
                        </div>
                    </div>`;
            });
            messagesHtml += '</div>';
        }

        // 课后产出物链接
        let outputsHtml = '';
        const outputItems = [];
        if (outputs.diaries && outputs.diaries.length > 0) {
            outputItems.push(`📝 ${outputs.diaries.length} 篇学习日记`);
        }
        if (outputs.summaries && outputs.summaries.length > 0) {
            outputItems.push(`📋 ${outputs.summaries.length} 份复习总结`);
        }
        if (outputs.group_chats && outputs.group_chats.length > 0) {
            outputItems.push(`💬 ${outputs.group_chats.length} 条群聊点评`);
        }
        if (outputs.annotations && outputs.annotations.length > 0) {
            outputItems.push(`🔍 ${outputs.annotations.length} 次划词问答`);
        }
        if (outputItems.length > 0) {
            outputsHtml = `
                <div class="history-chat-outputs">
                    <div style="font-size:13px;font-weight:600;color:var(--text-secondary);margin-bottom:8px">📦 课后产出</div>
                    <div style="display:flex;gap:8px;flex-wrap:wrap">${outputItems.map(item => `<span style="background:rgba(108,92,231,0.06);color:var(--accent);padding:4px 10px;border-radius:12px;font-size:12px">${item}</span>`).join('')}</div>
                </div>`;
        }

        content.innerHTML = headerHtml + messagesHtml + outputsHtml;
        modal.classList.add('active');
        modal.scrollTop = 0;
        if (content.querySelector('.history-chat-messages')) {
            content.querySelector('.history-chat-messages').scrollTop = 0;
        }
    } catch (e) {
        showToast('加载历史对话失败: ' + e.message, 'error');
    }
}

function closeHistoryChatModal() {
    document.getElementById('history-chat-modal').classList.remove('active');
}

function getStateLabel(state) {
    const labels = { 'SHARE': '📖 教师分享', 'PROBE': '❓ 教师提问', 'EXPLAIN': '📚 教师讲解', 'GUIDE': '🧭 教师引导', 'END': '🏁 结束', 'EVAL': '🔍 评估' };
    return labels[state] || state;
}

// ==================== 角色推荐 ====================
async function loadRoleRecommendation(courseId) {
    try {
        const courseDetail = await API.get(`/api/courses/${courseId}`);
        const course = courseDetail.course || {};
        const currentTeacherId = course.current_teacher || null;
        const result = await API.get(`/api/courses/${courseId}/roles/recommend`);
        const container = document.getElementById('contract-role-grid');
        const roles = await API.get('/api/roles');
        AppState.roles = roles;
        container.innerHTML = '';
        const recommended = result.recommended || [];
        const entries = Object.entries(roles);
        entries.sort(([a], [b]) => {
            const aCur = a === currentTeacherId ? -1 : 0;
            const bCur = b === currentTeacherId ? -1 : 0;
            const aRec = recommended.includes(a) ? 0 : 1;
            const bRec = recommended.includes(b) ? 0 : 1;
            return (aCur - bCur) || (aRec - bRec);
        });
        container.innerHTML = entries.map(([id, role]) => renderRoleCard(role, id, recommended, currentTeacherId)).join('');
        container.querySelectorAll('.select-role-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                const rid = btn.dataset.roleId;
                container.querySelectorAll('.role-card-wide').forEach(c => c.classList.remove('selected'));
                container.querySelector(`.role-card-wide[data-role-id="${rid}"]`).classList.add('selected');
            });
        });
        container.querySelectorAll('.view-role-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                viewRoleDetail(btn.dataset.roleId);
            });
        });
        container.querySelectorAll('.role-card-wide').forEach(card => {
            card.addEventListener('click', (e) => {
                if (e.target.closest('button')) return;
                container.querySelectorAll('.role-card-wide').forEach(c => c.classList.remove('selected'));
                card.classList.add('selected');
            });
        });
    } catch (e) {
        console.error('加载角色推荐失败', e);
    }
}

// ==================== 结业答辩 ====================
async function startDefense() {
    try {
        const result = await API.post(`/api/courses/${AppState.currentCourseId}/defense/start`, {});
        AppState.defenseQuestions = result.questions || [];
        AppState.defenseAnswers = [];

        switchView('defense');

        const container = document.getElementById('defense-questions');
        container.innerHTML = `<div style="text-align:center;padding:16px">
            <h2>🎓 结业答辩</h2>
            <p style="color:var(--text-secondary);margin:8px 0">⏱️ 限时 ${result.time_limit_minutes} 分钟 · 📝 共 ${result.questions.length} 道题</p>
            <div id="defense-timer" style="font-size:24px;font-weight:700;color:var(--accent);margin:12px 0">${result.time_limit_minutes}:00</div>
        </div>`;

        result.questions.forEach((q, i) => {
            const div = document.createElement('div');
            div.className = 'fade-in';
            div.style.cssText = 'background:var(--bg-secondary);border-radius:var(--radius);padding:20px;margin-bottom:16px;box-shadow:var(--shadow)';
            div.innerHTML = `
                <div style="font-weight:600;margin-bottom:8px">第 ${i + 1} 题</div>
                <div style="font-size:16px;margin-bottom:12px">${q.question}</div>
                <textarea id="defense-answer-${i}" rows="4" style="width:100%;padding:12px;border:1px solid var(--border);border-radius:var(--radius-sm);font-size:14px;font-family:inherit;resize:vertical" placeholder="请输入你的回答..."></textarea>
            `;
            container.appendChild(div);
        });

        // 提交按钮
        const submitBtn = document.createElement('div');
        submitBtn.style.cssText = 'text-align:center;padding:16px';
        submitBtn.innerHTML = `<button class="btn btn-primary" onclick="submitDefense()" style="font-size:16px;padding:12px 32px">📮 提交答辩</button>`;
        container.appendChild(submitBtn);

        // 启动计时器
        let minutes = result.time_limit_minutes;
        let seconds = 0;
        document.body.style.userSelect = 'none';
        document.addEventListener('copy', preventCopy);
        document.addEventListener('cut', preventCopy);
        document.addEventListener('contextmenu', preventCopy);

        AppState.defenseTimer = setInterval(() => {
            seconds--;
            if (seconds < 0) { minutes--; seconds = 59; }
            const timer = document.getElementById('defense-timer');
            if (timer) timer.textContent = `${minutes}:${seconds.toString().padStart(2, '0')}`;
            if (minutes <= 0 && seconds <= 0) {
                clearInterval(AppState.defenseTimer);
                showToast('⏰ 时间到！请提交答案', 'error');
                submitDefense();
            }
        }, 1000);

    } catch (e) {
        showToast('启动答辩失败: ' + e.message, 'error');
    }
}

function preventCopy(e) {
    e.preventDefault();
    showToast('答辩期间请闭卷作答', 'error');
}

async function submitDefense() {
    if (AppState.defenseTimer) {
        clearInterval(AppState.defenseTimer);
        AppState.defenseTimer = null;
    }

    document.body.style.userSelect = '';
    document.removeEventListener('copy', preventCopy);
    document.removeEventListener('cut', preventCopy);
    document.removeEventListener('contextmenu', preventCopy);

    const answers = AppState.defenseQuestions.map((q, i) => {
        const textarea = document.getElementById(`defense-answer-${i}`);
        return { question_index: i, answer: textarea ? textarea.value.trim() : '' };
    });

    try {
        const result = await API.post(`/api/courses/${AppState.currentCourseId}/defense/submit`, {
            questions: AppState.defenseQuestions,
            answers: answers,
        });

        const container = document.getElementById('defense-questions');
        container.innerHTML = '';

        if (result.all_passed) {
            container.innerHTML = `<div class="certificate-card">
                <div class="cert-icon">🎓</div>
                <h3>🎉 恭喜通过！</h3>
                ${result.certificate ? `
                    <div class="cert-meta"><strong>课程：</strong>${result.certificate.course_title}</div>
                    <div class="cert-meta"><strong>教师：</strong>${result.certificate.teacher}</div>
                    <div class="cert-meta"><strong>学习时长：</strong>${result.certificate.total_minutes} 分钟</div>
                    ${result.certificate.total_tokens ? `<div class="cert-meta"><strong>Token消耗：</strong>约 ${result.certificate.total_tokens >= 1000 ? (result.certificate.total_tokens/1000).toFixed(1)+'k' : result.certificate.total_tokens}</div>` : ''}
                    <div class="cert-meta"><strong>强项：</strong>${result.certificate.strengths}</div>
                    <div class="cert-meta"><strong>教师寄语：</strong>${result.certificate.teacher_comment}</div>
                    <div style="margin-top:16px;display:flex;gap:8px;justify-content:center">
                        <button class="btn btn-success" onclick="copyCertificate()">📋 复制证书</button>
                        <button class="btn btn-primary" onclick="window.print()">🖨️ 打印PDF</button>
                    </div>
                ` : ''}
            </div>`;

            showToast('🎉 恭喜毕业！', 'success');
        } else {
            container.innerHTML = `<div style="text-align:center;padding:40px">
                <div style="font-size:48px;margin-bottom:16px">😅</div>
                <h3>还需继续努力</h3>
                <p style="color:var(--text-secondary);margin:8px 0">有题目未通过，请回顾薄弱章节后再来挑战</p>
                <button class="btn btn-primary" onclick="openCourse('${AppState.currentCourseId}')">返回课程</button>
            </div>`;
            // 显示评估结果
            result.results.forEach(r => {
                container.innerHTML += `<div style="background:var(--bg-primary);padding:12px;border-radius:8px;margin:8px 0">
                    第${r.question_index + 1}题: <span style="color:${r.verdict === 'PASS' ? 'var(--success)' : 'var(--danger)'}">${r.verdict}</span> - ${r.comment}
                </div>`;
            });
            showToast('部分题目未通过，继续加油！', 'error');
        }
    } catch (e) {
        showToast('提交失败: ' + e.message, 'error');
    }
}

function copyCertificate() {
    const cert = document.querySelector('.certificate-card');
    if (cert) {
        const text = cert.textContent.trim();
        navigator.clipboard.writeText(text).then(() => {
            showToast('证书已复制！', 'success');
        });
    }
}

function renderCertificates(certificates) {
    const container = document.getElementById('certificates-list');
    container.innerHTML = '';
    if (certificates.length === 0) {
        container.innerHTML = '<div style="color:#636e72;font-size:13px">暂无证书</div>';
        return;
    }
    function fmt(d) { const dt = parseDBTime(d); return dt ? dt.toLocaleString('zh-CN',{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}) : ''; }
    certificates.forEach(c => {
        const mins = c.total_minutes || 0;
        const hrs = Math.floor(mins / 60);
        const timeStr = hrs > 0 ? `${hrs}小时${mins % 60}分钟` : `${mins}分钟`;
        const div = document.createElement('div');
        div.className = 'certificate-card';
        div.innerHTML = `
            <div class="cert-icon">🎓</div>
            <h3>${AppState.roles[c.teacher_role_id]?.name || c.teacher_role_id} · 结业证书</h3>
            <div class="cert-meta">📅 ${fmt(c.issued_at)}</div>
            <div class="cert-meta">⏱ 学习时长：${timeStr}</div>
            <div class="cert-meta">💪 强项：${c.strengths || '待总结'}</div>
        `;
        container.appendChild(div);
    });
}

// ==================== 设置弹窗 ====================
function showSettingsModal() {
    const modal = document.getElementById('settings-modal');
    modal.classList.add('active');

    API.get('/api/settings/llm').then(cfg => {
        document.getElementById('setting-api-key').value = cfg.has_api_key ? '已配置（已隐藏）' : '';
        document.getElementById('setting-base-url').value = cfg.base_url;
        document.getElementById('setting-model').value = cfg.model;
    }).catch(() => {});
}

async function saveSettings() {
    const apiKey = document.getElementById('setting-api-key').value.trim();
    const baseUrl = document.getElementById('setting-base-url').value.trim();
    const model = document.getElementById('setting-model').value.trim();

    const data = {};
    if (apiKey && apiKey !== '已配置（已隐藏）') data.api_key = apiKey;
    if (baseUrl) data.base_url = baseUrl;
    if (model) data.model = model;

    try {
        await API.post('/api/settings/llm', data);
        showToast('设置已保存', 'success');
    } catch (e) {
        showToast('保存失败', 'error');
    }
}

function closeSettingsModal() {
    document.getElementById('settings-modal').classList.remove('active');
}

// ==================== 删除课程 ====================
async function deleteCurrentCourse() {
    if (!confirm('确定要删除这门课程吗？所有学习数据将丢失。')) return;
    try {
        await API.delete(`/api/courses/${AppState.currentCourseId}`);
        AppState.currentCourseId = null;
        await loadCourseList();
        switchView('dashboard');
        showToast('课程已删除', 'success');
    } catch (e) {
        showToast('删除失败', 'error');
    }
}

// ==================== 空书架推荐 ====================
async function searchBooks() {
    const query = document.getElementById('book-search-input').value.trim();
    if (!query) { showToast('请输入课题名称', 'error'); return; }

    showToast('正在搜索推荐教材...');
    // 使用LLM推荐（通过后端）
    try {
        const roles = await API.get('/api/roles');
        // 简单关键词推荐
        const recommendations = [
            { title: `${query}（基础篇）`, author: '推荐阅读经典入门教材', channel: '各大书店/图书馆' },
            { title: `${query}（进阶篇）`, author: '适合有一定基础的读者', channel: '大学教材/专业书籍' },
            { title: `${query}（实践指南）`, author: '侧重实战应用', channel: '技术社区/在线课程平台' },
        ];

        const container = document.getElementById('recommend-results');
        container.innerHTML = recommendations.map((r, i) => `
            <div class="fade-in" style="background:var(--bg-secondary);border-radius:var(--radius-sm);padding:16px;margin-bottom:8px;box-shadow:var(--shadow)">
                <div style="font-weight:600;font-size:15px">📖 ${r.title}</div>
                <div style="font-size:13px;color:#636e72;margin:4px 0">${r.author}</div>
                <div style="font-size:12px;color:#b2bec3">获取渠道：${r.channel}</div>
                <button class="btn btn-sm btn-outline" style="margin-top:8px" onclick="quickCreateFromRecommend('${r.title}')">标记为想读</button>
            </div>
        `).join('');
        showToast('推荐完成！', 'success');
    } catch (e) {
        showToast('搜索失败', 'error');
    }
}

async function quickCreateFromRecommend(title) {
    try {
        const course = await API.post('/api/courses', {
            title: title,
            source_type: 'recommendation',
            source_path: '',
        });
        showToast('已添加到课程列表！', 'success');
        await loadCourseList();
        openCourse(course.course_id);
    } catch (e) {
        showToast('创建失败', 'error');
    }
}

// ==================== 初始化 ====================
document.addEventListener('DOMContentLoaded', function() {
    // 导航
    document.querySelectorAll('.nav-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            const view = btn.dataset.view;
            if (view === 'create-course') {
                showCreateCourse();
            } else {
                switchView(view);
                if (view === 'dashboard') loadDashboard();
            }
        });
    });

    // 文件上传
    setupFileUpload();

    // 划词系统
    setupAnnotationSystem();

    // 关闭弹窗（点击外部）
    document.querySelectorAll('.modal-overlay').forEach(modal => {
        modal.addEventListener('click', (e) => {
            if (e.target === modal) modal.classList.remove('active');
        });
    });

    // 输入框自动拉伸
    const chatInput = document.getElementById('chat-input');
    if (chatInput) {
        chatInput.addEventListener('input', function() {
            this.style.height = 'auto';
            this.style.height = Math.min(this.scrollHeight, 150) + 'px';
            document.getElementById('send-btn').disabled = !this.value.trim();
        });
        chatInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                sendChatMessage();
            }
        });
    }

    // 初始化加载
    loadDashboard();
    loadCourseList();
});