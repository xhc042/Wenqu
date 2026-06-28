/**
 * 问渠（Wenqu）v1.1 前端应用逻辑
 * 单页应用（SPA）主控制器
 */

// ==================== 状态管理 ====================
const AppState = {
    currentView: 'dashboard',
    currentCourseId: null,
    currentChapterIndex: null,
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
    selectedReadingMode: 'standard',
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
    async put(url, data) {
        const resp = await fetch(url, {
            method: 'PUT',
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

// ==================== 全局进度提示系统 ====================
let currentProgressOverlay = null;
let currentTaskProgressOverlay = null;

function showProgressOverlay(title, steps) {
    // 移除已有的进度覆盖层
    hideProgressOverlay();

    const overlay = document.createElement('div');
    overlay.id = 'progress-overlay';
    overlay.className = 'modal-overlay active';
    overlay.style.zIndex = '3000';
    overlay.style.backdropFilter = 'blur(4px)';

    overlay.innerHTML = `
        <div class="modal" style="width:520px;max-width:90vw">
            <div style="text-align:center;padding:24px 24px 16px">
                <div style="font-size:48px;margin-bottom:12px;animation:pulse 2s infinite">${getProgressIcon(title)}</div>
                <h2 style="margin:0 0 8px;font-size:18px;color:var(--text-primary)">${title}</h2>
                <p id="progress-current-step" style="margin:0 0 20px;font-size:14px;color:var(--text-secondary)">准备中...</p>
            </div>
            <div style="padding:0 24px 24px">
                <div class="progress-bar-outer" style="margin-bottom:20px;height:8px">
                    <div id="progress-bar-fill" class="progress-bar-inner" style="width:0%;transition:width 0.5s ease"></div>
                </div>
                <div id="progress-steps-list" style="max-height:240px;overflow-y:auto">
                    ${steps.map((s, i) => `
                        <div class="progress-step" data-step="${i}" style="display:flex;align-items:center;gap:10px;padding:8px 12px;border-radius:8px;margin-bottom:4px;font-size:13px;color:var(--text-secondary)">
                            <span class="step-icon" style="width:20px;text-align:center;font-size:14px">⏳</span>
                            <span class="step-text">${s}</span>
                        </div>
                    `).join('')}
                </div>
            </div>
        </div>
    `;

    document.body.appendChild(overlay);
    currentProgressOverlay = overlay;

    // 添加脉冲动画
    if (!document.getElementById('progress-anim-style')) {
        const style = document.createElement('style');
        style.id = 'progress-anim-style';
        style.textContent = `
            @keyframes pulse {
                0%, 100% { transform: scale(1); opacity: 1; }
                50% { transform: scale(1.1); opacity: 0.8; }
            }
            .progress-step.active { background: rgba(108,92,231,0.08); color: var(--text-primary); }
            .progress-step.done { color: var(--success); }
            .progress-step.done .step-icon { content: '✅'; }
        `;
        document.head.appendChild(style);
    }

    return {
        updateStep: (index, status, message) => {
            const stepEl = overlay.querySelector(`[data-step="${index}"]`);
            if (!stepEl) return;
            const iconEl = stepEl.querySelector('.step-icon');
            const textEl = stepEl.querySelector('.step-text');
            const currentMsg = document.getElementById('progress-current-step');

            if (status === 'done') {
                stepEl.className = 'progress-step done';
                iconEl.textContent = '✅';
            } else if (status === 'active') {
                stepEl.className = 'progress-step active';
                iconEl.textContent = '🔄';
                if (message) currentMsg.textContent = message;
            } else if (status === 'error') {
                stepEl.className = 'progress-step';
                stepEl.style.color = 'var(--danger)';
                iconEl.textContent = '❌';
            }
            if (message) textEl.textContent = message;
        },
        setProgress: (percent) => {
            const fill = overlay.querySelector('#progress-bar-fill');
            if (fill) fill.style.width = percent + '%';
        },
        setTotalSteps: (total) => {
            const fill = overlay.querySelector('#progress-bar-fill');
            if (fill) fill.style.width = (total / steps.length * 100) + '%';
        },
    };
}

function getProgressIcon(title) {
    if (!title) return '📚';
    const t = title.toLowerCase();
    if (t.includes('分章') || t.includes('chapter')) return '📑';
    if (t.includes('快照') || t.includes('snapshot')) return '🚀';
    if (t.includes('精华') || t.includes('highlight')) return '✨';
    if (t.includes('掌握') || t.includes('syllabus')) return '📋';
    return '⚙️';
}

function hideProgressOverlay() {
    if (currentProgressOverlay) {
        currentProgressOverlay.remove();
        currentProgressOverlay = null;
    }
}

// ==================== 异步任务轮询 ====================
let currentTaskPollingInterval = null;
let currentTaskId = null;

function startTaskPolling(taskId) {
    // 清除之前的轮询
    if (currentTaskPollingInterval) {
        clearInterval(currentTaskPollingInterval);
    }
    window.currentTaskId = taskId;
    
    // 显示进度覆盖层
    const progressCtrl = showProgressOverlay('正在智能分章...', ['分章处理', '生成教学大纲', '完成']);
    
    const poll = async () => {
        try {
            const task = await API.get(`/api/tasks/${taskId}`);
            
            // 更新进度条
            progressCtrl.setProgress(task.progress);
            
            // 更新步骤状态
            if (task.steps) {
                task.steps.forEach((step, index) => {
                    let status = 'pending';
                    if (step.status === 'done') status = 'done';
                    else if (step.status === 'processing' || step.status === 'error') status = 'active';
                    
                    const msg = step.detail || step.name;
                    progressCtrl.updateStep(index, status, msg);
                });
            }
            
            // 检查任务是否完成或失败
            if (task.status === 'completed') {
                clearInterval(currentTaskPollingInterval);
                currentTaskPollingInterval = null;
                window.currentTaskId = null;
                hideProgressOverlay();
                showToast('✅ 分章和大纲生成完成！', 'success');
                
                // 刷新课程数据
                if (AppState.currentCourseId) {
                    await openCourse(AppState.currentCourseId);
                }
            } else if (task.status === 'failed') {
                clearInterval(currentTaskPollingInterval);
                currentTaskPollingInterval = null;
                window.currentTaskId = null;
                progressCtrl.updateStep(task.current_step || 0, 'error', task.error || '任务失败');
                setTimeout(() => {
                    hideProgressOverlay();
                    showToast('❌ 分章失败: ' + (task.error || '未知错误'), 'error');
                }, 1500);
            }
        } catch (e) {
            console.warn('轮询任务状态失败:', e);
        }
    };
    
    // 立即执行一次，然后每2秒轮询一次
    poll();
    currentTaskPollingInterval = setInterval(poll, 2000);
    
    return currentTaskPollingInterval;
}

// ==================== 页面切换 ====================
function switchView(viewName) {
    // 切换页面时清理任务轮询
    if (currentTaskPollingInterval && viewName !== 'course-detail') {
        clearInterval(currentTaskPollingInterval);
        currentTaskPollingInterval = null;
        currentTaskId = null;
    }
    
    AppState.currentView = viewName;
    document.querySelectorAll('.page-panel').forEach(p => p.classList.remove('active'));
    const panel = document.getElementById(`page-${viewName}`);
    if (panel) panel.classList.add('active');

    document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
    const navBtn = document.querySelector(`.nav-btn[data-view="${viewName}"]`);
    if (navBtn) navBtn.classList.add('active');

    // 更新页面标题
    const titles = { dashboard: '📊 仪表盘', 'create-course': '➕ 创建课程', 'course-detail': '📖 课程详情', chat: '💬 对话', defense: '🎓 结业答辩', 'model-config': '🔧 模型配置' };
    const titleEl = document.getElementById('page-title');
    if (titleEl && titles[viewName]) titleEl.textContent = titles[viewName];
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

    // URL 输入时自动填入课程名称
    const urlInput = document.getElementById('url-input');
    if (urlInput) {
        urlInput.addEventListener('change', function() {
            const titleInput = document.getElementById('course-title-input');
            if (!titleInput.value.trim() && this.value.trim()) {
                try {
                    const url = new URL(this.value.trim());
                    // 尝试从路径中提取文件名作为书名
                    let name = url.pathname.split('/').pop() || url.hostname;
                    // 去掉常见扩展名
                    const exts = ['.txt', '.md', '.markdown', '.epub'];
                    for (const ext of exts) {
                        if (name.toLowerCase().endsWith(ext)) {
                            name = name.slice(0, -ext.length);
                            break;
                        }
                    }
                    // URL解码
                    try { name = decodeURIComponent(name); } catch(e) {}
                    if (name && name.length > 1) {
                        titleInput.value = name;
                    }
                } catch(e) {}
            }
        });
    }

    // 文本粘贴时自动提取第一行作为课程名称
    const textContent = document.getElementById('text-content');
    if (textContent) {
        textContent.addEventListener('input', function() {
            const titleInput = document.getElementById('course-title-input');
            if (!titleInput.value.trim() && this.value.trim()) {
                const firstLine = this.value.trim().split('\n')[0].trim();
                if (firstLine) {
                    titleInput.value = firstLine.slice(0, 50);
                }
            }
        });
    }
}

async function handleFile(file) {
    try {
        const result = await API.upload(file);
        document.getElementById('file-name').textContent = `已选择: ${file.name}`;
        document.getElementById('file-name').style.display = 'block';
        document.getElementById('file-path').value = result.file_path;
        document.getElementById('file-source-type').value = result.source_type;
        showToast('文件上传成功');

        // 自动填入文件名作为课程名称（去掉扩展名）
        const titleInput = document.getElementById('course-title-input');
        if (!titleInput.value.trim()) {
            let name = file.name;
            const exts = ['.txt', '.md', '.markdown', '.epub'];
            for (const ext of exts) {
                if (name.toLowerCase().endsWith(ext)) {
                    name = name.slice(0, -ext.length);
                    break;
                }
            }
            titleInput.value = name;
        }
    } catch (e) {
        showToast('上传失败: ' + e.message, 'error');
    }
}

// ==================== 阅读模式选择 ====================
function selectReadingMode(mode) {
    AppState.selectedReadingMode = mode;
    document.querySelectorAll('.mode-btn').forEach(btn => {
        btn.classList.toggle('selected', btn.dataset.mode === mode);
    });
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
            reading_mode: AppState.selectedReadingMode,
        });

        showToast('课程创建成功！', 'success');
        await loadCourseList();

        // 如果有内容，后端已自动启动异步分章任务，直接进入课程页面
        // 前端会通过轮询显示进度
        console.log('[创建课程] course_id:', course.course_id, 'task_id:', course.task_id);
        openCourse(course.course_id, course.task_id);
    } catch (e) {
        showToast('创建失败: ' + e.message, 'error');
    }
}

// ==================== 打开课程 ====================
async function openCourse(courseId, taskId = null) {
    console.log('[打开课程] courseId:', courseId, 'taskId:', taskId);
    
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

        // 立即渲染课程UI，不让用户等待
        renderCourseUI(course, data);
        
        // 优先使用传入的 taskId，否则检查是否有活跃任务
        const activeTaskId = taskId || data.active_task?.task_id;
        
        // 如果有异步任务，启动后台轮询更新进度（不阻塞页面）
        if (activeTaskId) {
            console.log('[启动后台轮询] taskId:', activeTaskId, 'courseId:', courseId);
            startBackgroundTaskPolling(activeTaskId, courseId);
        } else {
            console.log('[无异步任务] 直接显示课程');
        }
        
        // 历史数据异步加载，不阻塞页面显示
        API.get(`/api/courses/${courseId}/history`).then(historyData => {
            updateCourseHistory(historyData);
        }).catch(() => {});

    } catch (e) {
        showToast('加载课程失败', 'error');
    }
}

// 后台轮询任务进度，不阻塞页面
function startBackgroundTaskPolling(taskId, courseId) {
    let pollCount = 0;
    const maxPolls = 300; // 最多轮询10分钟（2秒*300）
    
    // 显示非阻塞状态提示（顶部小条幅）
    showNonBlockingProgress();
    
    const poll = async () => {
        if (pollCount >= maxPolls) {
            hideNonBlockingProgress();
            return;
        }
        
        try {
            const task = await API.get(`/api/tasks/${taskId}`);
            
            if (task.status === 'completed') {
                hideNonBlockingProgress();
                showToast('✅ 分章和大纲生成完成！', 'success');
                
                // 刷新课程数据
                const data = await API.get(`/api/courses/${courseId}`);
                renderCourseUI(data.course, data);
                return;
            } else if (task.status === 'failed') {
                hideNonBlockingProgress();
                showToast('⚠️ 分章失败: ' + (task.error || '未知错误'), 'error');
                // 分章失败后允许重试
                showRetryButton(courseId);
                return;
            }
            
            // 更新非阻塞进度显示
            updateNonBlockingProgress(task);
            pollCount++;
            setTimeout(poll, 2000);
        } catch (e) {
            console.warn('轮询任务状态失败:', e);
            pollCount++;
            setTimeout(poll, 2000);
        }
    };
    
    poll();
}

// 显示非阻塞式进度条（顶部小条幅，不遮挡用户操作）
function showNonBlockingProgress() {
    // 如果已存在，先移除
    hideNonBlockingProgress();
    
    const progressBar = document.createElement('div');
    progressBar.id = 'non-blocking-progress';
    progressBar.style.cssText = `
        position: fixed;
        top: 0;
        left: 0;
        right: 0;
        height: 3px;
        background: var(--border);
        z-index: 9999;
        overflow: hidden;
    `;
    progressBar.innerHTML = `
        <div id="non-blocking-progress-fill" style="
            height: 100%;
            width: 0%;
            background: linear-gradient(90deg, var(--accent), var(--success));
            transition: width 0.5s ease;
        "></div>
    `;
    document.body.appendChild(progressBar);
    
    // 顶部状态提示
    const statusBar = document.createElement('div');
    statusBar.id = 'non-blocking-status';
    statusBar.style.cssText = `
        position: fixed;
        top: 3px;
        right: 16px;
        background: var(--surface);
        border: 1px solid var(--border);
        border-radius: 20px;
        padding: 4px 12px;
        font-size: 12px;
        color: var(--text-secondary);
        z-index: 10000;
        display: flex;
        align-items: center;
        gap: 8px;
        box-shadow: 0 2px 8px rgba(0,0,0,0.1);
    `;
    statusBar.innerHTML = `<span>📚</span><span id="non-blocking-status-text">正在智能分章...</span>`;
    document.body.appendChild(statusBar);
}

// 更新非阻塞进度条
function updateNonBlockingProgress(task) {
    const fill = document.getElementById('non-blocking-progress-fill');
    const statusText = document.getElementById('non-blocking-status-text');
    
    if (fill) {
        fill.style.width = (task.progress || 0) + '%';
    }
    
    if (statusText && task.steps && task.steps[task.current_step || 0]) {
        const currentStep = task.steps[task.current_step || 0];
        statusText.textContent = currentStep.detail || currentStep.name || '处理中...';
    }
}

// 隐藏非阻塞进度条
function hideNonBlockingProgress() {
    const progressBar = document.getElementById('non-blocking-progress');
    const statusBar = document.getElementById('non-blocking-status');
    if (progressBar) progressBar.remove();
    if (statusBar) statusBar.remove();
}

// 显示重试按钮
function showRetryButton(courseId) {
    const chapterList = document.getElementById('chapter-list');
    if (chapterList) {
        chapterList.innerHTML = `
            <div class="empty-state">
                <div class="empty-icon">😔</div>
                <h3>分章失败</h3>
                <p>请稍后重试</p>
                <button class="btn btn-primary" onclick="retryChapterGeneration('${courseId}')" style="margin-top:16px">
                    🔄 重新分章
                </button>
            </div>
        `;
    }
}

// 重新分章
async function retryChapterGeneration(courseId) {
    try {
        const result = await API.post('/api/tasks/chapters-generate', { course_id: courseId });
        if (result.task_id) {
            showToast('正在重新分章...', 'info');
            startBackgroundTaskPolling(result.task_id, courseId);
        }
    } catch (e) {
        showToast('重试失败: ' + e.message, 'error');
    }
}

// 显示任务进度覆盖层
function showTaskProgressOverlay(task) {
    // 如果覆盖层已存在，只更新内容
    if (currentTaskProgressOverlay) {
        updateTaskProgressOverlay(task);
        return;
    }
    
    const overlay = document.createElement('div');
    overlay.id = 'task-progress-overlay';
    overlay.className = 'modal-overlay active';
    overlay.style.zIndex = '3000';
    overlay.style.backdropFilter = 'blur(4px)';
    
    const stepsHtml = (task.steps || []).map((step, i) => {
        let icon = '⏳';
        let className = 'progress-step';
        if (step.status === 'done') { icon = '✅'; className += ' done'; }
        else if (step.status === 'processing' || step.status === 'error') { icon = '🔄'; className += ' active'; }
        if (step.status === 'error') className += ' error';
        
        return `<div class="${className}" data-step="${i}" style="display:flex;align-items:center;gap:10px;padding:8px 12px;border-radius:8px;margin-bottom:4px;font-size:13px;color:var(--text-secondary)">
            <span class="step-icon" style="width:20px;text-align:center;font-size:14px">${icon}</span>
            <span class="step-text">${step.name}${step.detail ? ' - ' + step.detail : ''}</span>
        </div>`;
    }).join('');
    
    overlay.innerHTML = `
        <div class="modal" style="width:520px;max-width:90vw">
            <div style="text-align:center;padding:24px 24px 16px">
                <div style="font-size:48px;margin-bottom:12px;animation:pulse 2s infinite">📚</div>
                <h2 style="margin:0 0 8px;font-size:18px;color:var(--text-primary)">正在处理课程...</h2>
                <p style="margin:0 0 20px;font-size:14px;color:var(--text-secondary)">${task.steps ? task.steps[task.current_step || 0]?.detail || '准备中...' : '准备中...'}</p>
            </div>
            <div style="padding:0 24px 24px">
                <div class="progress-bar-outer" style="margin-bottom:20px;height:8px">
                    <div class="progress-bar-inner" style="width:${task.progress || 0}%;transition:width 0.5s ease"></div>
                </div>
                <div style="max-height:240px;overflow-y:auto">
                    ${stepsHtml}
                </div>
            </div>
        </div>
    `;
    
    document.body.appendChild(overlay);
    currentTaskProgressOverlay = overlay;
    
    // 添加脉冲动画
    if (!document.getElementById('task-progress-anim-style')) {
        const style = document.createElement('style');
        style.id = 'task-progress-anim-style';
        style.textContent = `
            @keyframes pulse {
                0%, 100% { transform: scale(1); opacity: 1; }
                50% { transform: scale(1.1); opacity: 0.8; }
            }
            .progress-step.active { background: rgba(108,92,231,0.08); color: var(--text-primary); }
            .progress-step.done { color: var(--success); }
            .progress-step.error { color: var(--danger); }
        `;
        document.head.appendChild(style);
    }
}

function updateTaskProgressOverlay(task) {
    if (!currentTaskProgressOverlay) return;
    
    // 更新进度条
    const fill = currentTaskProgressOverlay.querySelector('.progress-bar-inner');
    if (fill) fill.style.width = (task.progress || 0) + '%';
    
    // 更新当前提示文字
    const currentMsg = currentTaskProgressOverlay.querySelector('p[style*="margin:0 0 20px"]');
    if (currentMsg && task.steps && task.steps[task.current_step || 0]) {
        currentMsg.textContent = task.steps[task.current_step || 0].detail || '处理中...';
    }
    
    // 更新步骤状态
    (task.steps || []).forEach((step, i) => {
        const stepEl = currentTaskProgressOverlay.querySelector(`[data-step="${i}"]`);
        if (!stepEl) return;
        
        let icon = '⏳';
        let className = 'progress-step';
        if (step.status === 'done') { icon = '✅'; className += ' done'; }
        else if (step.status === 'processing' || step.status === 'error') { icon = '🔄'; className += ' active'; }
        if (step.status === 'error') className += ' error';
        
        stepEl.className = className;
        stepEl.querySelector('.step-icon').textContent = icon;
        const textEl = stepEl.querySelector('.step-text');
        if (textEl) {
            textEl.textContent = step.name + (step.detail ? ' - ' + step.detail : '');
        }
    });
}

function hideTaskProgressOverlay() {
    if (currentTaskProgressOverlay) {
        currentTaskProgressOverlay.remove();
        currentTaskProgressOverlay = null;
    }
}

// 渲染课程UI
function renderCourseUI(course, data) {
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
                <div class="course-source">${course.source_type} · ${new Date(course.created_at).toLocaleDateString()}
                    · <span style="color:var(--accent);font-weight:500">已学习 ${sessionCount} 次</span>
                </div>
                <div style="display:flex;gap:16px;margin-top:8px;font-size:13px;color:var(--text-secondary)">
                    <span>🧑‍🏫 教师：<strong>${teacherDisplay}</strong></span>
                    <span>⏱ 时长：<strong>${timeStr}</strong></span>
                    <span>🔤 Token：<strong>${tokensStr}</strong></span>
                </div>
            </div>
            <div style="display:flex;gap:8px">
                <button class="btn btn-outline btn-sm" onclick="showCourseOverview()">📊 全书总览</button>
                ${course.reading_mode === 'speed' ? '<button class="btn btn-outline btn-sm" onclick="showSnapshotsModal()">🚀 知识快照</button>' : ''}
                ${course.reading_mode === 'deep' ? '<button class="btn btn-outline btn-sm" onclick="showSpiritualNotesModal()">🧠 思辨笔记</button>' : ''}
                <button class="btn btn-outline btn-sm" onclick="showContractModal()">📋 学习契约</button>
                <button class="btn btn-outline btn-sm" onclick="showSlidersModal()">🎛️ 风格调控</button>
                <button class="btn btn-outline btn-sm" onclick="switchView('model-config');loadModelConfig()">🔧 模型配置</button>
                <button class="btn btn-outline btn-sm" onclick="showAllSyllabusModal()">📋 全书知识点</button>
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
    renderChapters(data.chapters || [], data.syllabus || [], {}, data.reading_mode);
    
    // 课后产出物
    renderPostClass(data);
    
    // 证书
    renderCertificates(data.certificates || []);
    
    // 推荐角色
    loadRoleRecommendation(course.id);
}

// 更新课程历史数据
function updateCourseHistory(historyData) {
    if (!historyData || !historyData.history) return;
    
    const courseId = AppState.currentCourseId;
    if (!courseId) return;
    
    // 统计每个章节的学习次数
    const chapterSessionCounts = {};
    historyData.history.forEach(s => {
        const idx = s.chapter_index;
        chapterSessionCounts[idx] = (chapterSessionCounts[idx] || 0) + 1;
    });
    
    // 重新渲染章节列表，带上学习次数
    API.get(`/api/courses/${courseId}`).then(data => {
        renderChapters(data.chapters || [], data.syllabus || [], chapterSessionCounts, data.reading_mode);
    }).catch(() => {});
}

function renderChapters(chapters, syllabus, chapterSessionCounts = {}, readingMode = null) {
    const list = document.getElementById('chapter-list');
    list.innerHTML = '';

    if (chapters.length === 0) {
        list.innerHTML = `
            <div class="chapter-loading-state" style="
                background: linear-gradient(135deg, rgba(108,92,231,0.05) 0%, rgba(0,0,0,0) 100%);
                border: 1px dashed var(--border);
                border-radius: 12px;
                padding: 40px;
                text-align: center;
                margin: 16px 0;
            ">
                <div style="font-size: 48px; margin-bottom: 16px; animation: pulse 2s infinite">📚</div>
                <h3 style="margin: 0 0 8px; color: var(--text-primary)">正在智能分章...</h3>
                <p style="margin: 0; color: var(--text-secondary); font-size: 14px">
                    系统正在分析文本结构，生成章节大纲<br>
                    <span style="font-size: 12px; color: var(--text-tertiary)">不用担心，你可以先看看课程简介，稍等片刻即可</span>
                </p>
                <div style="margin-top: 16px">
                    <div style="width: 200px; height: 4px; background: var(--border); border-radius: 2px; margin: 0 auto; overflow: hidden;">
                        <div style="width: 30%; height: 100%; background: var(--accent); border-radius: 2px; animation: loading 1.5s ease-in-out infinite;"></div>
                    </div>
                </div>
                <style>
                    @keyframes loading {
                        0% { transform: translateX(-100%); }
                        100% { transform: translateX(400%); }
                    }
                </style>
            </div>
        `;
        return;
    }

    const isSpeedMode = readingMode === 'speed';

    // 构建树形结构
    const tree = buildChapterTree(chapters);

    function renderTreeItems(items, depth) {
        return items.map(ch => {
            const chapterSyllabus = syllabus.filter(s => s.chapter_index === ch.idx);
            const mastered = chapterSyllabus.filter(s => s.status === 'mastered').length;
            const total = chapterSyllabus.length;
            const isLoaded = ch.is_loaded !== undefined ? ch.is_loaded : 1;
            const hasChildren = ch.children && ch.children.length > 0;
            const indent = depth * 20;

            const div = document.createElement('div');
            div.className = 'chapter-item';
            div.style.marginLeft = `${indent}px`;
            div.style.borderLeft = depth > 0 ? '2px solid var(--border)' : 'none';
            div.style.paddingLeft = depth > 0 ? '12px' : '16px';

            // 层级指示器
            const levelIcon = depth === 0 ? '📖' : depth === 1 ? '📄' : '📌';

            const sessionCount = chapterSessionCounts[ch.idx] || 0;

            // 速读模式：简洁展示，包含快照信息
            if (isSpeedMode) {
                // 构建核心信息标签
                const coreTags = [];
                if (ch.is_core) coreTags.push('<span class="tag tag-core" style="font-size:11px;background:rgba(255,107,107,0.15);color:#ff6b6b;border:none">⭐ 核心</span>');
                if (ch.importance && ch.importance > 0.7) coreTags.push('<span class="tag" style="font-size:11px;background:rgba(255,193,7,0.15);color:#ffc107;border:none">🔥 重要</span>');
                
                const snapshotInfo = [];
                if (ch.learning_goal) snapshotInfo.push(`<span style="font-size:12px;color:#636e72">🎯 ${escapeHtml(ch.learning_goal)}</span>`);
                if (ch.core_viewpoint) snapshotInfo.push(`<span style="font-size:12px;color:#636e72">💡 ${escapeHtml(ch.core_viewpoint)}</span>`);
                
                const keywordsHtml = (ch.keywords && ch.keywords.length > 0) 
                    ? `<div style="display:flex;flex-wrap:wrap;gap:4px;margin-top:4px">${ch.keywords.map(k => `<span style="font-size:11px;padding:2px 8px;background:rgba(108,92,231,0.1);color:var(--accent);border-radius:10px">${escapeHtml(k)}</span>`).join('')}</div>`
                    : '';

                div.innerHTML = `
                    <div class="chapter-index">${levelIcon}</div>
                    <div class="chapter-info">
                        <div class="chapter-title" style="font-weight:${depth <= 1 ? '600' : '400'}">
                            ${ch.title}
                            ${coreTags.join('')}
                        </div>
                        ${snapshotInfo.length > 0 ? `<div style="margin-top:4px">${snapshotInfo.join('<br>')}</div>` : ''}
                        ${keywordsHtml}
                        <div class="chapter-items-count" style="color:#636e72;font-size:12px;margin-top:4px">
                            ${sessionCount > 0 ? `<span style="color:var(--success)">✅ 已学习 ${sessionCount} 次</span>` : '<span>💡 快速浏览即可</span>'}
                        </div>
                    </div>
                    <div class="chapter-actions">
                        ${sessionCount > 0 ? `<button class="btn btn-outline btn-sm chapter-history-btn" onclick="event.stopPropagation();showChapterHistory(${ch.idx})" title="查看学习历史">📜</button>` : ''}
                        <button class="btn btn-outline btn-sm" onclick="startChat(${ch.idx})">
                            ${sessionCount > 0 ? '再次学习' : '快速浏览'}
                        </button>
                    </div>
                `;
            } else {
                // 细读/研读模式：完整展示
                div.innerHTML = `
                    <div class="chapter-index">${levelIcon}</div>
                    <div class="chapter-info">
                        <div class="chapter-title" style="font-weight:${depth <= 1 ? '600' : '400'}">
                            ${ch.title}
                            ${!isLoaded ? '<span class="tag tag-pending" style="font-size:11px;margin-left:6px">⏳ 未加载</span>' : ''}
                            ${isLoaded && ch.summary ? '<span style="font-size:11px;color:#636e72;display:block;margin-top:2px">' + ch.summary.slice(0, 60) + '</span>' : ''}
                        </div>
                        <div class="chapter-items-count">
                            ${(() => {
                                const wordCount = ch.content_slice ? ch.content_slice.length : 0;
                                const wordStr = wordCount >= 1000 ? (wordCount / 1000).toFixed(1) + 'k' : wordCount.toString();
                                const wordDisplay = wordCount > 0 ? `<span style="color:#636e72;font-size:11px;margin-right:8px">📝 ${wordStr}字</span>` : '';
                                const masteryDisplay = total > 0 ? `${mastered}/${total} 项掌握` : (isLoaded ? '暂无掌握项' : '点击"开始学习"加载内容');
                                return wordDisplay + masteryDisplay;
                            })()}
                            <span style="margin-left:8px">
                                ${chapterSyllabus.map(s => {
                                const displayText = s.description.slice(0, 60) + (s.description.length > 60 ? '...' : '');
                                const isMastered = s.status === 'mastered';
                                return `<span class="tag tag-${isMastered ? 'mastered' : s.status === 'in_progress' ? 'progress' : 'pending'}" style="margin:0 2px;cursor:pointer;font-size:11px" title="${s.description}" onclick="viewSyllabusDetail(${s.id}, this.title)">${displayText}${isMastered ? ' ✅' : ''}</span>` +
                                    (isMastered ? '' : `<button class="btn btn-xs" style="font-size:10px;padding:0 4px;margin-left:1px;vertical-align:middle;border:none;background:var(--success);color:#fff;border-radius:3px;cursor:pointer" onclick="event.stopPropagation();markSyllabusMastered(${s.id}, this)" title="标记为已掌握">✓</button>`);
                            }).join(' ')}
                            </span>
                        </div>
                    </div>
                    <div class="chapter-actions">
                        ${sessionCount > 0 ? `<button class="btn btn-outline btn-sm chapter-history-btn" onclick="event.stopPropagation();showChapterHistory(${ch.idx})" title="查看学习历史(${sessionCount}次)">📜 ${sessionCount}</button>` : ''}
                        <button class="btn btn-primary btn-sm" onclick="startChat(${ch.idx})">
                            ${!isLoaded ? '⏳ 加载并学习' : '开始学习'}
                        </button>
                    </div>
                `;
            }
            list.appendChild(div);

            // 递归渲染子节点
            if (hasChildren) {
                renderTreeItems(ch.children, depth + 1);
            }

            return div;
        });
    }

    renderTreeItems(tree, 0);
}

function buildChapterTree(chapters) {
    // 构建层级树
    const map = {};
    const roots = [];

    chapters.forEach(ch => {
        map[ch.idx] = { ...ch, children: [] };
    });

    chapters.forEach(ch => {
        const parentIdx = ch.parent_idx !== undefined && ch.parent_idx !== null && ch.parent_idx >= 0 ? ch.parent_idx : -1;
        if (parentIdx >= 0 && map[parentIdx]) {
            map[parentIdx].children.push(map[ch.idx]);
        } else {
            roots.push(map[ch.idx]);
        }
    });

    // 如果所有章节都是平级的（parent_idx === -1），直接返回
    return roots.length > 0 ? roots : chapters.map(ch => ({ ...ch, children: [] }));
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
    let courseData;

    // 获取章节信息用于显示
    let chapterTitle = `第 ${chapterIndex + 1} 章`;
    try {
        courseData = await API.get(`/api/courses/${courseId}`);
        const chapters = courseData.chapters || [];
        const chapter = chapters.find(c => c.idx === chapterIndex);

        // 如果章节不存在（还在分章中），提示用户等待
        if (!chapter) {
            showToast('📚 章节正在生成分章中，请稍候...', 'info');
            
            // 如果有后台任务在运行，显示进度
            if (window.currentTaskId) {
                const task = await API.get(`/api/tasks/${window.currentTaskId}`).catch(() => null);
                if (task && task.status === 'processing') {
                    showToast(`⏳ ${task.steps?.[task.current_step || 0]?.detail || '分章中...'}`, 'info');
                }
            }
            return;
        }

        // 更新对话页顶部章节名称
        document.getElementById('chat-chapter-name').textContent = chapter?.title || chapterTitle;

        // 检查章节是否已加载，未加载则触发懒加载
        if (chapter && !chapter.is_loaded) {
            showToast('正在加载本章内容...');
            await API.post(`/api/courses/${courseId}/chapters/${chapterIndex}/load`, {});
            showToast('章节内容已就绪！', 'success');
        }
    } catch (e) {
        console.warn('章节信息加载失败，继续启动对话', e);
        document.getElementById('chat-chapter-name').textContent = chapterTitle;
    }

    // 使用已获取的 courseData，避免重复请求
    if (!courseData) {
        courseData = await API.get(`/api/courses/${courseId}`);
    }
    const course = courseData;
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
        AppState.currentChapterIndex = chapterIndex;

        switchView('chat');
        document.getElementById('chat-messages').innerHTML = '';
        document.getElementById('chat-status').textContent = '正在建立连接...';
        document.getElementById('chat-input').disabled = true;
        document.getElementById('send-btn').disabled = true;
        document.getElementById('mastered-btn').style.display = 'none';

        connectWebSocket(result.session_id);

        // 如果有下一章，提前加载（苏格拉底预演）
        autoPreviewNextChapter(courseId, chapterIndex, courseData?.chapters || []);
    } catch (e) {
        showToast('启动对话失败: ' + e.message, 'error');
    }
}

// ==================== 章节历史对话列表 ====================
async function showChapterHistory(chapterIdx) {
    const courseId = AppState.currentCourseId;

    try {
        const [courseData, historyData] = await Promise.all([
            API.get(`/api/courses/${courseId}`),
            API.get(`/api/courses/${courseId}/history`)
        ]);

        const chapters = courseData.chapters || [];
        const chapter = chapters.find(c => c.idx === chapterIdx);
        const chapterTitle = chapter?.title || `第 ${chapterIdx + 1} 章`;

        const sessions = historyData.history || [];
        const chapterSessions = sessions.filter(s => s.chapter_index === chapterIdx);

        const modal = document.getElementById('history-chat-modal');
        const content = document.getElementById('history-chat-content');

        // 更新弹窗标题
        modal.querySelector('h2').textContent = `📖 ${chapterTitle}`;

        if (chapterSessions.length === 0) {
            content.innerHTML = `
                <div class="history-empty">
                    <div class="history-empty-icon">📚</div>
                    <h3>暂无学习历史</h3>
                    <p>开始本章学习后，对话记录会显示在这里</p>
                </div>
            `;
            modal.classList.add('active');
            return;
        }

        let html = `
            <div class="history-list-header">
                <div style="font-size:14px;color:var(--text-secondary);margin-bottom:12px">
                    本章共 <strong>${chapterSessions.length}</strong> 次学习记录
                </div>
            </div>
        `;

        chapterSessions.forEach((s, idx) => {
            const teacher = AppState.roles?.[s.teacher_role_id] || { emoji: '🎓', name: s.teacher_role_id };
            const dateStr = s.started_at ? new Date(s.started_at).toLocaleString('zh-CN', {
                year: 'numeric', month: '2-digit', day: '2-digit',
                hour: '2-digit', minute: '2-digit'
            }) : '未知时间';
            const duration = s.duration_minutes || 0;
            const rounds = s.total_rounds || 0;

            html += `
                <div class="history-list-item" onclick="viewChapterSessionHistory('${s.session_id}', ${chapterIdx})">
                    <div class="history-list-icon">${teacher.emoji}</div>
                    <div class="history-list-info">
                        <div class="history-list-title">
                            第 ${idx + 1} 次学习 · ${dateStr}
                        </div>
                        <div class="history-list-meta">
                            <span>👤 ${teacher.name}</span>
                            <span>💬 ${rounds} 轮</span>
                            ${duration ? `<span>⏱️ ${duration}分钟</span>` : ''}
                            ${s.has_summary ? '<span>📋 有总结</span>' : ''}
                            ${s.has_diary ? '<span>📝 有日记</span>' : ''}
                        </div>
                    </div>
                    <div class="history-list-arrow">›</div>
                </div>
            `;
        });

        content.innerHTML = html;
        modal.classList.add('active');
    } catch (e) {
        showToast('加载历史失败: ' + e.message, 'error');
    }
}

// 查看章节历史会话详情
async function viewChapterSessionHistory(sessionId, chapterIdx) {
    try {
        const data = await API.get(`/api/sessions/${sessionId}`);
        const session = data.session;
        const messages = data.messages || [];

        const modal = document.getElementById('history-chat-modal');
        const content = document.getElementById('history-chat-content');

        // 获取章节标题
        let chapterTitle = `第 ${chapterIdx + 1} 章`;
        if (AppState.currentCourseId) {
            try {
                const courseData = await API.get(`/api/courses/${AppState.currentCourseId}`);
                const chapter = courseData.chapters?.find(c => c.idx === chapterIdx);
                if (chapter?.title) chapterTitle = chapter.title;
            } catch(e) {}
        }

        // 会话头部信息
        const dateStr = session.started_at ? new Date(session.started_at).toLocaleString('zh-CN', {
            year: 'numeric', month: '2-digit', day: '2-digit',
            hour: '2-digit', minute: '2-digit'
        }) : '未知';
        const duration = session.total_duration || 0;

        // 更新弹窗标题
        modal.querySelector('h2').textContent = `📖 ${chapterTitle} - 历史对话`;

        let headerHtml = `
            <div class="history-detail-header">
                <div class="history-detail-back" onclick="showChapterHistory(${chapterIdx})">
                    ‹ 返回学习历史
                </div>
                <div style="display:flex;align-items:center;gap:12px;margin:16px 0">
                    <span style="font-size:32px">${session.teacher_info?.emoji || '🎓'}</span>
                    <div>
                        <div style="font-size:18px;font-weight:700">${session.teacher_info?.name || '导师'}</div>
                        <div style="font-size:13px;color:var(--text-secondary)">${session.teacher_info?.style || ''}</div>
                    </div>
                </div>
                <div style="display:flex;flex-wrap:wrap;gap:8px;font-size:13px;color:var(--text-secondary)">
                    <span>📖 ${chapterTitle}</span>
                    <span>· 💬 ${session.total_rounds || 0} 轮对话</span>
                    ${duration ? `<span>· ⏱️ ${duration} 分钟</span>` : ''}
                    <span>· 📅 ${dateStr}</span>
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
                const avatar = isUser ? '👤' : (session.teacher_info?.emoji || '🎓');
                const time = m.created_at ? new Date(m.created_at).toLocaleTimeString('zh-CN', {hour:'2-digit', minute:'2-digit'}) : '';

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

        content.innerHTML = headerHtml + messagesHtml;
        modal.scrollTop = 0;
    } catch (e) {
        showToast('加载历史对话失败: ' + e.message, 'error');
    }
}

// ==================== 对话内历史对话列表 ====================
async function showChatHistory() {
    const courseId = AppState.currentCourseId;
    const chapterIdx = AppState.currentChapterIndex;

    try {
        const data = await API.get(`/api/courses/${courseId}/history`);
        const sessions = data.history || [];

        // 按章节分组
        const chapterSessions = sessions.filter(s => s.chapter_index === chapterIdx);

        if (chapterSessions.length === 0) {
            const modal = document.getElementById('history-chat-modal');
            const content = document.getElementById('history-chat-content');
            content.innerHTML = `
                <div class="history-empty">
                    <div class="history-empty-icon">📚</div>
                    <h3>暂无历史对话</h3>
                    <p>开始本章学习后，对话记录会显示在这里</p>
                </div>
            `;
            modal.classList.add('active');
            return;
        }

        const modal = document.getElementById('history-chat-modal');
        const content = document.getElementById('history-chat-content');

        let html = `<div class="history-list-header">
            <div style="font-size:14px;color:var(--text-secondary);margin-bottom:12px">
                当前章节共 <strong>${chapterSessions.length}</strong> 次学习记录
            </div>
        </div>`;

        chapterSessions.forEach((s, idx) => {
            const teacher = AppState.roles?.[s.teacher_role_id] || { emoji: '🎓', name: s.teacher_role_id };
            const dateStr = s.started_at ? new Date(s.started_at).toLocaleString('zh-CN', {
                year: 'numeric', month: '2-digit', day: '2-digit',
                hour: '2-digit', minute: '2-digit'
            }) : '未知时间';
            const duration = s.total_duration || s.duration_minutes || 0;
            const rounds = s.total_rounds || 0;

            html += `
                <div class="history-list-item" onclick="viewSessionHistory('${s.session_id}')">
                    <div class="history-list-icon">${teacher.emoji}</div>
                    <div class="history-list-info">
                        <div class="history-list-title">
                            第 ${idx + 1} 次学习 · ${dateStr}
                        </div>
                        <div class="history-list-meta">
                            <span>👤 ${teacher.name}</span>
                            <span>💬 ${rounds} 轮</span>
                            ${duration ? `<span>⏱️ ${duration}分钟</span>` : ''}
                            ${s.has_summary ? '<span>📋 有总结</span>' : ''}
                            ${s.has_diary ? '<span>📝 有日记</span>' : ''}
                        </div>
                    </div>
                    <div class="history-list-arrow">›</div>
                </div>
            `;
        });

        content.innerHTML = html;
        modal.classList.add('active');
    } catch (e) {
        showToast('加载历史对话失败: ' + e.message, 'error');
    }
}

// 查看单个历史会话详情
async function viewSessionHistory(sessionId) {
    try {
        const data = await API.get(`/api/sessions/${sessionId}`);
        const session = data.session;
        const messages = data.messages || [];

        const modal = document.getElementById('history-chat-modal');
        const content = document.getElementById('history-chat-content');

        // 获取章节标题
        let chapterTitle = `第 ${session.chapter_index + 1} 章`;
        if (AppState.currentCourseId) {
            try {
                const courseData = await API.get(`/api/courses/${AppState.currentCourseId}`);
                const chapter = courseData.chapters?.find(c => c.idx === session.chapter_index);
                if (chapter?.title) chapterTitle = chapter.title;
            } catch(e) {}
        }

        // 会话头部信息
        const dateStr = session.started_at ? new Date(session.started_at).toLocaleString('zh-CN', {
            year: 'numeric', month: '2-digit', day: '2-digit',
            hour: '2-digit', minute: '2-digit'
        }) : '未知';
        const duration = session.total_duration || 0;

        let headerHtml = `
            <div class="history-detail-header">
                <div class="history-detail-back" onclick="showChatHistory()">
                    ‹ 返回历史列表
                </div>
                <div style="display:flex;align-items:center;gap:12px;margin:16px 0">
                    <span style="font-size:32px">${session.teacher_info?.emoji || '🎓'}</span>
                    <div>
                        <div style="font-size:18px;font-weight:700">${session.teacher_info?.name || '导师'}</div>
                        <div style="font-size:13px;color:var(--text-secondary)">${session.teacher_info?.style || ''}</div>
                    </div>
                </div>
                <div style="display:flex;flex-wrap:wrap;gap:8px;font-size:13px;color:var(--text-secondary)">
                    <span>📖 ${chapterTitle}</span>
                    <span>· 💬 ${session.total_rounds || 0} 轮对话</span>
                    ${duration ? `<span>· ⏱️ ${duration} 分钟</span>` : ''}
                    <span>· 📅 ${dateStr}</span>
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
                const avatar = isUser ? '👤' : (session.teacher_info?.emoji || '🎓');
                const time = m.created_at ? new Date(m.created_at).toLocaleTimeString('zh-CN', {hour:'2-digit', minute:'2-digit'}) : '';

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

        content.innerHTML = headerHtml + messagesHtml;
        modal.scrollTop = 0;
    } catch (e) {
        showToast('加载历史对话失败: ' + e.message, 'error');
    }
}

function closeHistoryChatModal() {
    document.getElementById('history-chat-modal').classList.remove('active');
    // 重置弹窗标题
    document.getElementById('history-chat-modal').querySelector('h2').textContent = '💬 历史对话';
}

function getStateLabel(state) {
    const labels = {
        'SHARE': '📖 教师分享',
        'PROBE': '❓ 教师提问',
        'EXPLAIN': '📚 教师讲解',
        'GUIDE': '🧭 教师引导',
        'END': '🏁 结束',
        'EVAL': '🔍 评估'
    };
    return labels[state] || state;
}

async function autoPreviewNextChapter(courseId, currentIdx, chapters) {
    try {
        const nextChapter = chapters.find(c => c.idx === currentIdx + 1);
        if (!nextChapter) return;
        // 异步预加载下一章内容（静默，不影响当前体验）
        if (!nextChapter.is_loaded) {
            API.post(`/api/courses/${courseId}/chapters/${currentIdx + 1}/load`, {}).catch(() => {});
        }
    } catch (e) {
        // 静默失败
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
            document.getElementById('mastered-btn').style.display = 'block';
            document.getElementById('chat-status').textContent = '等待你的回答...';
            document.getElementById('chat-input').focus();
        } else if (msg.state === 'EXPLAIN' || msg.state === 'GUIDE') {
            appendStreamContent(msg.state, msg.content);
        } else if (msg.state === 'END') {
            // END消息内容显示
        } else if (msg.state === 'MASTERED_SKIPPED') {
            // 快速跳过成功，自动标记已掌握
            if (msg.syllabus_id) {
                showToast('✅ 已标记为掌握，跳过到下一题', 'success');
            }
        } else if (msg.state === 'SESSION_END') {
            document.getElementById('chat-status').textContent = '🎉 课程结束！';
            document.getElementById('chat-input').disabled = true;
            document.getElementById('send-btn').disabled = true;
            document.getElementById('mastered-btn').style.display = 'none';
            AppState.isChatting = false;

            // 显示课后处理进度
            showPostClassProgress();

            // 刷新页面数据
            openCourse(AppState.currentCourseId);

            // 等待课后闭环完成后，弹出苏格拉底预演
            waitForPostClassComplete().catch(() => {}).then(async () => {
                const chIdx = AppState.currentChapterIndex;
                if (chIdx !== undefined && chIdx !== null) {
                    try {
                        const preview = await API.get(`/api/courses/${AppState.currentCourseId}/chapters/${chIdx}/preview`);
                        if (preview.preview || (preview.questions && preview.questions.length > 0)) {
                            showSocraticPreview(preview);
                        }
                    } catch (e) {
                        // 静默失败
                    }
                }
            });
            showToast('学习记录已生成中...', 'success');
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

    // 初始化或检查当前消息的状态
    if (!lastMsg || lastMsg.dataset.state !== state || lastMsg.dataset.finalized === 'true') {
        const div = document.createElement('div');
        div.className = 'message';
        div.dataset.state = state;
        div.dataset.finalized = 'false';
        div.dataset.inThink = 'false';
        div.dataset.thinkBuffer = '';

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
                <div class="thinking-fold" style="display:none">
                    <div class="thinking-header" onclick="toggleThinking(this.parentElement)">
                        <span>🤔 导师思考过程</span>
                        <span class="thinking-toggle">▼ 点击展开</span>
                    </div>
                    <div class="thinking-content"></div>
                </div>
            </div>
        `;
        container.appendChild(div);
        lastMsg = div;
    }

    const contentDiv = lastMsg.querySelector('.msg-content');
    const thinkingFold = lastMsg.querySelector('.thinking-fold');
    const thinkingContent = lastMsg.querySelector('.thinking-content');

    // 处理思考标签（可能跨多个chunk）
    let buffer = lastMsg.dataset.thinkBuffer + content;

    // 检查是否进入或退出思考模式
    const inThink = lastMsg.dataset.inThink === 'true';

    if (buffer.includes('<think>')) {
        // 进入思考模式
        lastMsg.dataset.inThink = 'true';

        // 提取思考前的正常内容
        const parts = buffer.split('<think>');
        if (parts[0]) {
            contentDiv.textContent += parts[0];
        }

        // 更新buffer为思考内容部分
        buffer = parts.slice(1).join('<think>');

        // 检查是否有思考结束标签
        if (buffer.includes('</think>')) {
            const thinkParts = buffer.split('</think>');
            lastMsg.dataset.thinkBuffer = thinkParts.slice(1).join('</think>'); // 剩余内容
            const thinkText = thinkParts[0];
            if (thinkText) {
                thinkingContent.textContent += thinkText;
            }
            thinkingFold.style.display = 'block';
            lastMsg.dataset.inThink = 'false';
        } else {
            // 思考内容还在进行中，缓存起来
            lastMsg.dataset.thinkBuffer = buffer;
        }
    } else if (inThink && buffer.includes('</think>')) {
        // 继续在思考中，但遇到了结束标签
        const thinkParts = buffer.split('</think>');
        thinkingContent.textContent += thinkParts[0];
        thinkingFold.style.display = 'block';
        lastMsg.dataset.thinkBuffer = thinkParts.slice(1).join('</think>');
        lastMsg.dataset.inThink = 'false';

        // 递归处理剩余内容
        if (lastMsg.dataset.thinkBuffer) {
            appendStreamContent(state, '');
        }
    } else if (inThink) {
        // 继续在思考中，追加到思考内容
        thinkingContent.textContent += buffer;
        lastMsg.dataset.thinkBuffer = '';
    } else {
        // 正常内容
        contentDiv.textContent += buffer;
        lastMsg.dataset.thinkBuffer = '';
    }

    container.scrollTop = container.scrollHeight;
}

// 切换思考内容的展开/折叠
function toggleThinking(foldEl) {
    const content = foldEl.querySelector('.thinking-content');
    const toggle = foldEl.querySelector('.thinking-toggle');
    const header = foldEl.querySelector('.thinking-header');

    if (content.style.display === 'none') {
        content.style.display = 'block';
        toggle.textContent = '▲ 点击收起';
        header.classList.add('expanded');
    } else {
        content.style.display = 'none';
        toggle.textContent = '▼ 点击展开';
        header.classList.remove('expanded');
    }
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

// ==================== 课后闭环进度展示 ====================
let postClassProgressTimer = null;

function showPostClassProgress() {
    // 在聊天区域底部插入进度提示
    const container = document.getElementById('chat-messages');
    const progressDiv = document.createElement('div');
    progressDiv.id = 'post-class-progress';
    progressDiv.className = 'post-class-progress';
    progressDiv.innerHTML = `
        <div class="post-class-progress-title">
            🎉 本节课学习完成！
        </div>
        <div class="post-class-progress-steps">
            <div class="progress-step" id="step-profile">
                <span class="step-icon">⏳</span>
                <span class="step-text">分析学习画像...</span>
            </div>
            <div class="progress-step" id="step-diary">
                <span class="step-icon">⏳</span>
                <span class="step-text">生成学习日记...</span>
            </div>
            <div class="progress-step" id="step-group-chat">
                <span class="step-icon">⏳</span>
                <span class="step-text">记录教师点评...</span>
            </div>
            <div class="progress-step" id="step-summary">
                <span class="step-icon">⏳</span>
                <span class="step-text">生成复习总结...</span>
            </div>
        </div>
        <div class="post-class-progress-hint">
            💡 您可以先查看课程主页，已生成的记录会逐步显示
        </div>
        <button class="btn btn-sm btn-primary" style="margin-top:8px" onclick="navigateToCourseDetail()">
            📖 返回课程主页
        </button>
    `;
    container.appendChild(progressDiv);
    container.scrollTop = container.scrollHeight;

    // 轮询检查每个步骤的完成状态
    postClassProgressTimer = setInterval(updatePostClassProgress, 1500);
}

async function updatePostClassProgress() {
    try {
        const sessionId = AppState.currentSessionId;
        if (!sessionId) return;

        // 获取日记、群聊、总结
        const diaryData = await API.get(`/api/courses/${AppState.currentCourseId}/diaries`);
        const groupChatData = await API.get(`/api/courses/${AppState.currentCourseId}/group-chats`);
        const summaryData = await API.get(`/api/courses/${AppState.currentCourseId}/summaries`);

        // 检查各项是否完成
        const hasDiary = (diaryData.diaries || []).some(d => d.session_id === sessionId);
        const hasGroupChat = (groupChatData.group_chats || []).some(g => g.session_id === sessionId);
        const hasSummary = (summaryData.summaries || []).some(s => s.session_id === sessionId);

        updateStep('step-profile', true);
        if (hasDiary) updateStep('step-diary', true);
        if (hasGroupChat) updateStep('step-group-chat', true);
        if (hasSummary) updateStep('step-summary', true);

        // 全部完成后停止轮询
        if (hasDiary && hasGroupChat && hasSummary) {
            clearInterval(postClassProgressTimer);
            postClassProgressTimer = null;
            // 显示完成状态
            const progressDiv = document.getElementById('post-class-progress');
            if (progressDiv) {
                progressDiv.classList.add('completed');
                const hint = progressDiv.querySelector('.post-class-progress-hint');
                if (hint) hint.innerHTML = '✅ 学习记录已生成完毕！';
            }
        }
    } catch (e) {
        console.warn('检查课后进度失败', e);
    }
}

function updateStep(stepId, completed) {
    const step = document.getElementById(stepId);
    if (!step) return;
    if (completed) {
        step.classList.add('completed');
        step.querySelector('.step-icon').textContent = '✅';
        step.querySelector('.step-text').style.color = 'var(--text-secondary)';
    }
}

function navigateToCourseDetail() {
    // 立即跳转课程主页（不等生成完成）
    clearInterval(postClassProgressTimer);
    postClassProgressTimer = null;
    openCourse(AppState.currentCourseId);
}

// 等待课后闭环完成（最多30秒）
async function waitForPostClassComplete() {
    const startTime = Date.now();
    const maxWait = 30000;
    while (Date.now() - startTime < maxWait) {
        await new Promise(resolve => setTimeout(resolve, 2000));
        try {
            const data = await API.get(`/api/courses/${AppState.currentCourseId}`);
            // 检查是否可以判断完成（通过刷新页面来检查日记/总结）
            const diaryData = await API.get(`/api/courses/${AppState.currentCourseId}/diaries`);
            const summaryData = await API.get(`/api/courses/${AppState.currentCourseId}/summaries`);
            if (diaryData.diaries && summaryData.summaries) {
                // 认为完成
                return true;
            }
        } catch (e) {
            // 忽略
        }
    }
    return false;
}

// 快速标记已掌握并跳过当前问题
async function sendMasteredQuick() {
    const input = document.getElementById('chat-input');
    const text = "我会了，这个知识点我已经掌握，继续下一个。";

    if (!AppState.ws || AppState.ws.readyState !== WebSocket.OPEN) return;

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

    // 隐藏"我已掌握"按钮
    document.getElementById('mastered-btn').style.display = 'none';

    // 标记上一条AI消息为已完成
    const lastMsg = container.lastElementChild.previousElementSibling;
    if (lastMsg) lastMsg.dataset.finalized = 'true';

    // 发送到WebSocket（发送特殊标记让后端知道这是快速跳过）
    AppState.ws.send(JSON.stringify({ content: text, quick_mastered: true }));

    input.value = '';
    input.disabled = true;
    document.getElementById('send-btn').disabled = true;
    document.getElementById('chat-status').textContent = 'AI正在思考...';
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
        const d = new Date(dateStr);
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
                <div style="font-size:13px;white-space:pre-wrap">${escapeHtml(s.content)}</div>
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

        function fmtTime(d) { return d ? new Date(d).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}) : ''; }

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
                        <span style="font-size:12px;color:#636e72">${new Date(dr.created_at).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'})} · ${qs.length}题 ${allPassed ? '✅全过' : `✅${passCount} ❌${failCount}`}</span>
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
                    return `<div style="margin:4px 0;font-size:12px">💬 ${tName}: ${escapeHtml(g.message).slice(0, 60)}... <span style="color:#b2bec3">${fmtTime(g.created_at)}</span></div>`;
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
            </div>`;
        }).join('');

        container.innerHTML = html;
    } catch (e) {
        console.error('加载学习历史失败', e);
    }
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
    function fmt(d) { return new Date(d).toLocaleString('zh-CN',{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}); }
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

// ==================== 模型配置 ====================

async function loadModelConfig() {
    try {
        const [providers, active] = await Promise.all([
            API.get('/api/llm/providers'),
            API.get('/api/llm/active'),
        ]);
        renderModelConfigStatus(active);
        renderProviderList(providers, active);
    } catch (e) {
        console.error('加载模型配置失败', e);
        document.getElementById('model-config-content').innerHTML = '<div class="empty-state"><p>加载失败</p></div>';
    }
}

function renderModelConfigStatus(active) {
    const el = document.getElementById('model-config-status');
    if (active && active.provider && active.model) {
        el.className = 'model-config-status active';
        el.innerHTML = `
            <div class="model-config-status-icon">✅</div>
            <div class="model-config-status-text">
                <strong>当前配置：</strong>${active.provider.name} / ${active.model.name}
                ${active.has_api_key ? '' : ' <span style="color:#e17055">（未设置 API Key）</span>'}
            </div>
        `;
    } else {
        el.className = 'model-config-status inactive';
        el.innerHTML = `
            <div class="model-config-status-icon">⚠️</div>
            <div class="model-config-status-text">尚未配置模型，请添加一个提供商并激活模型</div>
        `;
    }
}

function renderProviderList(providers, active) {
    const container = document.getElementById('model-config-content');
    if (!providers || providers.length === 0) {
        container.innerHTML = `
            <div class="empty-state" style="padding:40px">
                <div class="empty-icon">🔧</div>
                <h3>还没有提供商</h3>
                <p>添加一个 API 提供商开始使用</p>
                <button class="btn btn-primary" onclick="showAddProviderModal()" style="margin-top:12px">➕ 添加提供商</button>
            </div>
        `;
        return;
    }

    const activeModelId = active?.model?.id || null;
    let html = '<div style="margin-bottom:16px"><button class="btn btn-primary" onclick="showAddProviderModal()">➕ 添加提供商</button></div>';

    providers.forEach(p => {
        const isActiveProvider = p.is_active === 1;
        html += renderProviderCard(p, p.models || [], activeModelId, isActiveProvider);
    });

    container.innerHTML = html;
}

function renderProviderCard(provider, models, activeModelId, isActiveProvider) {
    const hasKey = provider.api_key ? '已配置' : '未配置';
    const modelsHtml = models.length
        ? models.map(m => renderModelRow(m, m.id === activeModelId && isActiveProvider)).join('')
        : '<div style="padding:8px;color:var(--text-secondary);font-size:13px">暂无模型</div>';

    return `
        <div class="provider-card ${isActiveProvider ? 'active' : ''}">
            <div class="provider-card-header">
                <div>
                    <div class="provider-card-name">
                        🤖 ${provider.name}
                        ${isActiveProvider ? '<span class="model-row-active-badge">当前</span>' : ''}
                    </div>
                    <div class="provider-card-meta">
                        ${provider.base_url} · API Key: ${hasKey}
                    </div>
                </div>
                <div class="provider-card-actions">
                    ${!isActiveProvider ? `<button class="btn btn-sm btn-success" onclick="activateProvider(${provider.id})">激活</button>` : ''}
                    <button class="btn btn-sm btn-outline" onclick="showEditProviderModal(${provider.id})">编辑</button>
                    <button class="btn btn-sm btn-danger" onclick="deleteProvider(${provider.id})">删除</button>
                    <button class="btn btn-sm btn-outline" onclick="testConnection(${provider.id})">测试连接</button>
                </div>
            </div>
            <div class="provider-card-models">
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
                    <span style="font-size:13px;font-weight:500;color:var(--text-secondary)">模型列表</span>
                    <button class="btn btn-sm btn-outline" onclick="showAddModelModal(${provider.id})">➕ 添加模型</button>
                </div>
                ${modelsHtml}
            </div>
        </div>
    `;
}

function renderModelRow(model, isActive) {
    return `
        <div class="model-row ${isActive ? 'active' : ''}">
            <div>
                <span class="model-row-name">${model.name}</span>
                ${isActive ? '<span class="model-row-active-badge">当前 ★</span>' : ''}
            </div>
            <div class="model-row-actions">
                ${!isActive ? `<button class="btn btn-sm btn-success" onclick="activateModel(${model.id})">激活</button>` : ''}
                <button class="btn btn-sm btn-danger" onclick="deleteModel(${model.id})">删除</button>
            </div>
        </div>
    `;
}

// ===== Provider 操作 =====

function showAddProviderModal() {
    document.getElementById('provider-modal-id').value = '';
    document.getElementById('provider-modal-title').textContent = '添加提供商';
    document.getElementById('provider-name').value = '';
    document.getElementById('provider-base-url').value = '';
    document.getElementById('provider-api-key').value = '';
    document.getElementById('provider-modal').classList.add('active');
}

async function showEditProviderModal(id) {
    try {
        const providers = await API.get('/api/llm/providers');
        const p = providers.find(x => x.id === id);
        if (!p) return;
        document.getElementById('provider-modal-id').value = id;
        document.getElementById('provider-modal-title').textContent = '编辑提供商';
        document.getElementById('provider-name').value = p.name;
        document.getElementById('provider-base-url').value = p.base_url;
        document.getElementById('provider-api-key').value = p.api_key ? '已配置（已隐藏）' : '';
        document.getElementById('provider-modal').classList.add('active');
    } catch (e) {
        showToast('加载失败', 'error');
    }
}

async function saveProvider() {
    const id = document.getElementById('provider-modal-id').value;
    const name = document.getElementById('provider-name').value.trim();
    const baseUrl = document.getElementById('provider-base-url').value.trim();
    let apiKey = document.getElementById('provider-api-key').value.trim();

    if (!name || !baseUrl) {
        showToast('名称和 Base URL 不能为空', 'error');
        return;
    }

    if (apiKey === '已配置（已隐藏）') apiKey = '';

    try {
        if (id) {
            const data = { name, base_url: baseUrl };
            if (apiKey) data.api_key = apiKey;
            await API.put(`/api/llm/providers/${id}`, data);
            showToast('提供商已更新', 'success');
        } else {
            await API.post('/api/llm/providers', { name, base_url: baseUrl, api_key: apiKey });
            showToast('提供商已添加', 'success');
        }
        closeProviderModal();
        loadModelConfig();
    } catch (e) {
        showToast('保存失败', 'error');
    }
}

async function deleteProvider(id) {
    if (!confirm('确定要删除此提供商及其所有模型吗？')) return;
    try {
        await API.delete(`/api/llm/providers/${id}`);
        showToast('已删除', 'success');
        loadModelConfig();
    } catch (e) {
        showToast('删除失败', 'error');
    }
}

function closeProviderModal() {
    document.getElementById('provider-modal').classList.remove('active');
}

// ===== Model 操作 =====

function showAddModelModal(providerId) {
    document.getElementById('model-modal-provider-id').value = providerId;
    document.getElementById('model-name').value = '';
    document.getElementById('model-modal').classList.add('active');
}

async function saveModel() {
    const providerId = document.getElementById('model-modal-provider-id').value;
    const name = document.getElementById('model-name').value.trim();
    if (!name) {
        showToast('模型名称不能为空', 'error');
        return;
    }
    try {
        await API.post(`/api/llm/providers/${providerId}/models`, { name });
        showToast('模型已添加', 'success');
        closeModelModal();
        loadModelConfig();
    } catch (e) {
        showToast('保存失败', 'error');
    }
}

async function deleteModel(id) {
    if (!confirm('确定要删除此模型吗？')) return;
    try {
        await API.delete(`/api/llm/models/${id}`);
        showToast('已删除', 'success');
        loadModelConfig();
    } catch (e) {
        showToast('删除失败', 'error');
    }
}

function closeModelModal() {
    document.getElementById('model-modal').classList.remove('active');
}

// ===== 激活 =====

async function activateProvider(id) {
    try {
        await API.post(`/api/llm/providers/${id}/activate`);
        showToast('已切换提供商', 'success');
        loadModelConfig();
    } catch (e) {
        showToast('激活失败', 'error');
    }
}

async function activateModel(id) {
    try {
        await API.post(`/api/llm/models/${id}/activate`);
        showToast('已切换模型', 'success');
        loadModelConfig();
    } catch (e) {
        showToast('激活失败', 'error');
    }
}

// ===== 测试连接 =====

async function testConnection(providerId) {
    try {
        const result = await API.post('/api/llm/test', { provider_id: providerId });
        if (result.success) {
            showToast(result.message, 'success');
        } else {
            showToast(result.message, 'error');
        }
    } catch (e) {
        showToast('测试请求失败', 'error');
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
            } else if (view === 'model-config') {
                switchView(view);
                loadModelConfig();
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

// ==================== 查看全书知识点 ====================
let allSyllabusCache = [];

function showAllSyllabusModal() {
    var modal = document.createElement('div');
    modal.className = 'modal-overlay active';
    modal.style.zIndex = '2000';
    var container = document.createElement('div');
    container.className = 'modal';
    container.onclick = function(e) { e.stopPropagation(); };
    container.innerHTML = '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:24px"><h2>📋 全书知识点</h2><div style="display:flex;gap:8px"><button class="btn btn-outline btn-sm" id="syllabus-refresh-btn" onclick="refreshSyllabusData()">🔄 刷新知识点</button><button class="btn btn-outline btn-sm" onclick="this.closest(\'.modal-overlay\').remove()">✕</button></div></div><div id="syllabus-list-modal" style="max-height:60vh;overflow-y:auto;padding-right:8px"></div>';
    modal.appendChild(container);
    document.body.appendChild(modal);
    loadAllSyllabusData();
}

async function loadAllSyllabusData() {
    if (!AppState.currentCourseId) return;
    try {
        var data = await API.get('/api/courses/' + AppState.currentCourseId);
        var items = data.syllabus || [];
        var el = document.getElementById('syllabus-list-modal');
        if (!el) return;
        if (items.length === 0) { el.innerHTML = '<div style="text-align:center;color:var(--text-secondary);padding:40px">暂无知识点</div>'; return; }
        var chapters = data.chapters || [];
        var chapterMap = {};
        chapters.forEach(function(c) { chapterMap[c.idx] = c.title; });
        el.innerHTML = items.map(function(s, idx) {
            var chTitle = chapterMap[s.chapter_index] || '第' + (s.chapter_index + 1) + '章';
            var st = s.status === 'mastered' ? '已掌握' : s.status === 'in_progress' ? '进行中' : '待学习';
            var bg = s.status === 'mastered' ? 'rgba(0,184,148,0.1)' : s.status === 'in_progress' ? 'rgba(253,203,110,0.1)' : 'rgba(222,230,233,0.1)';
            var cl = s.status === 'mastered' ? 'var(--success)' : s.status === 'in_progress' ? '#d68910' : '#636e72';
            var bd = s.status === 'mastered' ? 'var(--success)' : s.status === 'in_progress' ? '#d68910' : '#dfe6e9';
            return '<div style="background:var(--bg-secondary);border-radius:var(--radius-sm);padding:16px;margin-bottom:12px;border-left:3px solid var(--accent);box-shadow:var(--shadow)">' +
                '<div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:8px">' +
                '<div><span style="font-size:12px;color:var(--accent);font-weight:500">第' + (idx + 1) + '个知识点</span>' +
                '<span style="font-size:12px;color:#636e72;margin-left:8px">' + chTitle + '</span></div>' +
                '<div style="display:flex;align-items:center;gap:6px">' +
                (s.status === 'mastered' ? '' : '<button class="btn btn-xs" style="font-size:10px;padding:2px 8px;border:none;background:var(--success);color:#fff;border-radius:4px;cursor:pointer" onclick="event.stopPropagation();markSyllabusMastered(' + s.id + ', this)">✓ 我已掌握</button>') +
                '<span style="font-size:11px;padding:2px 8px;border-radius:4px;background:' + bg + ';color:' + cl + ';border:1px solid ' + bd + '">' + st + '</span></div></div>' +
                '<div class="syllabus-item" style="font-size:14px;color:var(--text-primary);line-height:1.6;cursor:pointer" data-id="' + s.id + '" data-desc="' + escapeHtml(s.description) + '">' +
                escapeHtml(s.description) + '</div></div>';
        }).join('');
        el.querySelectorAll('.syllabus-item').forEach(function(item) {
            item.onclick = function() {
                viewSyllabusDetail(parseInt(item.dataset.id), item.dataset.desc);
            };
        });
    } catch (e) { console.error('加载知识点失败', e); showToast('加载知识点失败', 'error'); }
}

async function refreshSyllabusData() {
    if (!AppState.currentCourseId) return;
    const btn = document.getElementById('syllabus-refresh-btn');
    if (!btn) return;
    btn.disabled = true;
    btn.textContent = '⏳ 生成中...';
    try {
        await API.post(`/api/courses/${AppState.currentCourseId}/syllabus/regenerate`, {});
        showToast('知识点已刷新', 'success');
        await loadAllSyllabusData();
    } catch (e) {
        console.error('刷新知识点失败', e);
        showToast('刷新失败: ' + (e.message || '未知错误'), 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = '🔄 刷新知识点';
    }
}

function viewSyllabusDetail(syllabusId, description) {
    if (!description) return;
    var modal = document.createElement('div');
    modal.className = 'modal-overlay active';
    modal.style.zIndex = '2001';
    var el = document.createElement('div');
    el.className = 'modal';
    el.style.maxWidth = '600px';
    el.onclick = function(e) { e.stopPropagation(); };
    el.innerHTML = '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:24px"><h2>📖 知识点详情</h2><button class="btn btn-outline btn-sm" onclick="this.closest(\'.modal-overlay\').remove()">✕</button></div>' +
        '<div style="font-size:16px;line-height:1.8;color:var(--text-primary);margin-bottom:24px;padding:20px;background:var(--bg-primary);border-radius:var(--radius-sm);border-left:4px solid var(--accent)">' + escapeHtml(description) + '</div>' +
        '<div style="display:flex;justify-content:flex-end;gap:8px">' +
        '<button class="btn btn-outline" onclick="this.closest(\'.modal-overlay\').remove()">关闭</button>' +
        '<button class="btn btn-primary" onclick="markSyllabusMastered(' + syllabusId + ', this)">✅ 我已掌握</button>' +
        '</div>';
    modal.appendChild(el);
    document.body.appendChild(modal);
}

async function markSyllabusMastered(syllabusId, btn) {
    if (!syllabusId) return;
    try {
        await API.patch('/api/syllabus/' + syllabusId, { status: 'mastered' });
        showToast('🎉 已标记为掌握！', 'success');
        // 关闭弹窗
        var modal = btn.closest('.modal-overlay');
        if (modal) modal.remove();
        // 刷新课程页面
        loadCourseDetail(AppState.currentCourseId);
    } catch (e) {
        showToast('标记失败', 'error');
    }
}

// ==================== 苏格拉底式预演弹窗 ====================
function showSocraticPreview(preview) {
    if (!preview || (!preview.preview && (!preview.questions || preview.questions.length === 0))) return;

    const questions = preview.questions || [];
    const nextTitle = preview.next_title || '下一章';

    // 构建弹窗内容
    let html = `<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px">
        <h2 style="margin:0">🎯 苏格拉底式预演</h2>
        <button class="btn btn-outline btn-sm" onclick="this.closest('.modal-overlay').remove()">✕</button>
    </div>
    <div style="background:linear-gradient(135deg, #667eea20, #764ba220);border-radius:var(--radius-sm);padding:20px;margin-bottom:16px">
        <div style="font-size:14px;color:var(--text-secondary);margin-bottom:8px">下一章：<strong>${escapeHtml(nextTitle)}</strong></div>
        <div style="font-size:15px;line-height:1.7;color:var(--text-primary)">
            🧠 带着以下问题去学习，效率会更高：
        </div>
    </div>`;

    if (questions.length > 0) {
        html += '<div style="margin-top:12px">';
        questions.forEach((q, i) => {
            html += `<div style="background:var(--bg-secondary);border-radius:var(--radius-sm);padding:14px 16px;margin-bottom:8px;border-left:3px solid var(--accent)">
                <div style="font-size:13px;color:var(--accent);font-weight:500;margin-bottom:4px">🤔 思考题 ${i + 1}</div>
                <div style="font-size:14px;line-height:1.6">${escapeHtml(q)}</div>
            </div>`;
        });
        html += '</div>';
    }

    html += `<div style="display:flex;justify-content:flex-end;gap:8px;margin-top:16px">
        <button class="btn btn-primary" onclick="this.closest('.modal-overlay').remove()">好的，我知道了</button>
    </div>`;

    const modal = document.createElement('div');
    modal.className = 'modal-overlay active';
    modal.style.zIndex = '2002';
    const container = document.createElement('div');
    container.className = 'modal';
    container.style.maxWidth = '520px';
    container.onclick = function(e) { e.stopPropagation(); };
    container.innerHTML = html;
    modal.appendChild(container);
    document.body.appendChild(modal);
}

// escapeHtml 已在第1009行定义，此处复用

// ==================== 全书总览弹窗 ====================
function showCourseOverview() {
    const modal = document.createElement('div');
    modal.className = 'modal-overlay active';
    modal.style.zIndex = '2000';
    modal.onclick = function(e) { if (e.target === modal) modal.remove(); };

    const container = document.createElement('div');
    container.className = 'modal';
    container.style.maxWidth = '700px';
    container.style.maxHeight = '80vh';
    container.style.overflow = 'auto';
    container.innerHTML = '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:20px"><h2>📊 全书总览</h2><button class="btn btn-outline btn-sm" onclick="this.closest(\'.modal-overlay\').remove()">✕</button></div><div id="course-overview-modal" style="min-height:200px;text-align:center;color:var(--text-secondary)">加载中...</div>';

    modal.appendChild(container);
    document.body.appendChild(modal);

    loadCourseOverviewModal();
}

// ==================== 速读模式：知识快照弹窗 ====================
function showSnapshotsModal() {
    const modal = document.createElement('div');
    modal.className = 'modal-overlay active';
    modal.style.zIndex = '2000';
    modal.onclick = function(e) { if (e.target === modal) modal.remove(); };

    const container = document.createElement('div');
    container.className = 'modal';
    container.style.maxWidth = '800px';
    container.style.maxHeight = '85vh';
    container.style.overflow = 'auto';
    container.innerHTML = '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:20px"><h2>🚀 知识快照</h2><button class="btn btn-outline btn-sm" onclick="this.closest(\'.modal-overlay\').remove()">✕</button></div><div id="snapshots-modal-content" style="min-height:200px;text-align:center;color:var(--text-secondary)">加载中...</div>';

    modal.appendChild(container);
    document.body.appendChild(modal);

    loadSnapshotsModal();
}

async function loadSnapshotsModal() {
    if (!AppState.currentCourseId) return;
    try {
        const data = await API.get(`/api/courses/${AppState.currentCourseId}/snapshots`);
        renderSnapshotsModal(data.snapshots || [], data.highlights);
    } catch (e) {
        console.error('加载知识快照失败', e);
        document.getElementById('snapshots-modal-content').innerHTML = '<div style="color:var(--danger)">加载失败</div>';
    }
}

function renderSnapshotsModal(snapshots, highlights) {
    const container = document.getElementById('snapshots-modal-content');
    if (!container) return;

    if (!snapshots || snapshots.length === 0) {
        container.innerHTML = '<div style="padding:40px;text-align:center;color:var(--text-secondary)">暂无知识快照<br><br>速读模式分章后会自动生成</div>';
        return;
    }

    let html = '';

    // 全局精华
    if (highlights && highlights.key_points && highlights.key_points.length > 0) {
        html += `
            <div class="highlights-card" style="margin-bottom:20px">
                <h3 style="color:var(--accent);margin-bottom:12px">📊 全书核心知识点</h3>
                <div style="display:flex;flex-direction:column;gap:8px">
                    ${highlights.key_points.map((point, i) => `
                        <div style="display:flex;align-items:flex-start;gap:8px;padding:8px 12px;background:var(--bg-secondary);border-radius:8px;border-left:3px solid ${i < 3 ? 'var(--accent)' : 'var(--border)'}">
                            <span style="font-size:11px;padding:2px 8px;border-radius:4px;font-weight:600;white-space:nowrap;background:${i < 3 ? 'rgba(108,92,231,0.15)' : 'rgba(99,110,114,0.15)'};color:${i < 3 ? 'var(--accent)' : 'var(--text-secondary)'}">${i < 3 ? '🔥 核心' : '📌 重要'}</span>
                            <span style="font-size:14px">${escapeHtml(point)}</span>
                        </div>
                    `).join('')}
                </div>
                ${highlights.chapter_priorities && highlights.chapter_priorities.length > 0 ? `
                    <div style="margin-top:16px;padding-top:16px;border-top:1px solid var(--border)">
                        <h4 style="font-size:14px;margin-bottom:8px">📚 推荐学习顺序</h4>
                        <ol style="padding-left:20px">${highlights.chapter_priorities.map(p => `<li style="margin-bottom:4px;font-size:13px">${escapeHtml(p)}</li>`).join('')}</ol>
                    </div>
                ` : ''}
            </div>
        `;
    }

    // 章节快照（按学习顺序排序）
    html += '<h3 style="margin-bottom:12px;font-size:15px">📑 各章知识快照</h3>';
    html += '<div style="display:flex;flex-direction:column;gap:10px">';
    
    // 按重要性排序
    const sortedSnapshots = [...snapshots].sort((a, b) => (b.importance || 0) - (a.importance || 0));
    
    html += sortedSnapshots.map((s, idx) => {
        const isCore = s.importance > 0.7;
        return `
        <div style="background:var(--bg-secondary);border-radius:12px;padding:14px 16px;box-shadow:0 2px 8px rgba(0,0,0,0.05);${isCore ? 'border-left:3px solid var(--danger);' : ''}">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">
                <div style="display:flex;align-items:center;gap:8px">
                    <span style="font-weight:600;color:var(--accent)">${escapeHtml(s.chapter_title || `第${s.chapter_index + 1}章`)}</span>
                    ${isCore ? '<span style="font-size:10px;padding:2px 6px;background:rgba(255,107,107,0.15);color:#ff6b6b;border-radius:4px;font-weight:600">⭐ 核心</span>' : ''}
                    <span style="font-size:11px;color:var(--text-secondary)">重要性: ${(s.importance || 0).toFixed(1)}</span>
                </div>
            </div>
            ${s.learning_goal ? `<div style="font-size:12px;color:var(--text-secondary);margin-bottom:4px">🎯 学习目标：${escapeHtml(s.learning_goal)}</div>` : ''}
            ${s.core_viewpoint ? `<div style="font-size:12px;color:var(--text-secondary);margin-bottom:4px">💡 核心观点：${escapeHtml(s.core_viewpoint)}</div>` : ''}
            <div style="display:flex;flex-wrap:wrap;gap:4px;margin-top:6px">
                ${(s.keywords || []).map(k => `<span style="font-size:11px;padding:3px 8px;background:rgba(108,92,231,0.1);color:var(--accent);border-radius:10px">${escapeHtml(k)}</span>`).join('')}
            </div>
        </div>
    `}).join('');
    html += '</div>';

    container.innerHTML = html;
}

async function loadCourseOverviewModal() {
    if (!AppState.currentCourseId) return;
    try {
        const overview = await API.get(`/api/courses/${AppState.currentCourseId}/overview`);
        renderCourseOverviewModal(overview);
    } catch (e) {
        console.error('加载课程概览失败', e);
        document.getElementById('course-overview-modal').innerHTML = '<div style="color:var(--danger)">加载失败</div>';
    }
}

function renderCourseOverviewModal(overview) {
    const container = document.getElementById('course-overview-modal');
    if (!container) return;

    const coverage = overview.knowledge_coverage || { total_points: 0, mastered_points: 0, percent: 0 };
    const percent = coverage.percent || 0;
    const circumference = 2 * Math.PI * 45;
    const offset = circumference - (circumference * percent / 100);

    // 速读模式显示快照数量，其他模式显示知识点数量
    const isSpeedMode = overview.reading_mode === 'speed';
    const totalLabel = isSpeedMode ? '快照' : '知识点';
    const totalCount = coverage.total_points || 0;
    const masteredCount = coverage.mastered_points || 0;

    let html = '';

    // === 第一部分：核心概览卡片（聚焦最重要信息）===
    html += `
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:20px">
            <div style="background:linear-gradient(135deg,rgba(108,92,231,0.1),rgba(108,92,231,0.05));border-radius:12px;padding:20px;text-align:center">
                <div style="font-size:36px;font-weight:700;color:var(--accent)">${percent}%</div>
                <div style="font-size:13px;color:var(--text-secondary);margin-top:4px">${isSpeedMode ? '知识覆盖率' : '掌握率'}</div>
                <div style="font-size:12px;color:var(--text-secondary);margin-top:2px">${masteredCount}/${totalCount} ${totalLabel}</div>
            </div>
            <div style="background:linear-gradient(135deg,rgba(0,184,148,0.1),rgba(0,184,148,0.05));border-radius:12px;padding:20px;text-align:center">
                <div style="font-size:36px;font-weight:700;color:var(--success)">${coverage.total_chapters || overview.chapter_stats?.length || 0}</div>
                <div style="font-size:13px;color:var(--text-secondary);margin-top:4px">总章节</div>
                <div style="font-size:12px;color:var(--text-secondary);margin-top:2px">全书共 ${coverage.total_chapters || overview.chapter_stats?.length || 0} 章</div>
            </div>
        </div>
    `;

    // === 第二部分：全书核心知识点（最重要，置顶显示）===
    if (isSpeedMode && overview.highlights && overview.highlights.key_points && overview.highlights.key_points.length > 0) {
        const h = overview.highlights;
        html += `
            <div style="margin-bottom:20px;padding:16px;background:linear-gradient(135deg, rgba(108,92,231,0.08), rgba(6,214,160,0.08));border-radius:12px;border:1px solid rgba(108,92,231,0.2)">
                <h3 style="margin:0 0 12px;font-size:15px;color:var(--accent)">🔥 全书核心知识点</h3>
                <div style="display:flex;flex-direction:column;gap:6px">
                    ${h.key_points.slice(0, 8).map((point, i) => `
                        <div style="display:flex;align-items:flex-start;gap:8px;padding:8px 12px;background:white;border-radius:8px">
                            <span style="font-size:11px;padding:2px 8px;border-radius:4px;font-weight:600;white-space:nowrap;background:${i < 3 ? 'rgba(255,107,107,0.15)' : 'rgba(108,92,231,0.1)'};color:${i < 3 ? '#ff6b6b' : 'var(--accent)'}">${i < 3 ? '🔥 核心' : '📌 重要'}</span>
                            <span style="font-size:13px;line-height:1.5">${escapeHtml(point)}</span>
                        </div>
                    `).join('')}
                </div>
            </div>
        `;
    }

    // === 第三部分：智能学习路径（速读模式专用）===
    if (isSpeedMode && overview.recommended_order && typeof overview.recommended_order === 'object' && !Array.isArray(overview.recommended_order)) {
        const order = overview.recommended_order;
        
        // 下一步推荐（最醒目）
        const nextCh = order.next_chapter;
        if (nextCh && nextCh.idx !== undefined) {
            html += `
                <div style="margin-bottom:20px;padding:16px;background:linear-gradient(135deg,rgba(253,203,110,0.15),rgba(253,203,110,0.05));border-radius:12px;border:2px solid rgba(255,193,7,0.3)">
                    <div style="font-size:13px;font-weight:600;color:#d68910;margin-bottom:8px">🎯 立即学习</div>
                    <div style="font-size:16px;font-weight:600">第${nextCh.idx + 1}章「${escapeHtml(nextCh.title || '')}」</div>
                    ${nextCh.learning_goal ? `<div style="font-size:13px;color:var(--text-secondary);margin-top:4px">🎯 学习目标：${escapeHtml(nextCh.learning_goal)}</div>` : ''}
                    ${nextCh.core_viewpoint ? `<div style="font-size:13px;color:var(--text-secondary);margin-top:2px">💡 核心观点：${escapeHtml(nextCh.core_viewpoint)}</div>` : ''}
                    <button class="btn btn-primary btn-sm" style="margin-top:12px" onclick="hideProgressOverlay();switchView('course-detail');setTimeout(()=>startChat(${nextCh.idx}),300)">开始学习 →</button>
                </div>
            `;
        }

        // 核心待学章节
        if (order.core_to_learn && order.core_to_learn.length > 0) {
            html += `
                <div style="margin-bottom:20px">
                    <h3 style="margin:0 0 10px;font-size:14px;color:var(--danger)">🔥 核心待学 (${order.core_to_learn.length}章)</h3>
                    ${order.core_to_learn.slice(0, 5).map((ch, idx) => {
                        const depsBadge = ch.deps_satisfied
                            ? '<span style="font-size:10px;color:var(--success)">✓ 可学</span>'
                            : '<span style="font-size:10px;color:var(--warning)">⏳ 需先学</span>';
                        return `
                            <div style="display:flex;align-items:center;gap:8px;padding:8px 12px;background:rgba(255,107,107,0.04);border-radius:8px;margin-bottom:4px;border-left:3px solid var(--danger)">
                                <span style="font-size:13px;font-weight:bold;color:var(--danger);min-width:20px">${idx + 1}.</span>
                                <span style="flex:1;font-size:13px">第${ch.idx + 1}章「${escapeHtml(ch.title || '')}」</span>
                                ${ch.learning_goal ? `<span style="font-size:11px;color:var(--text-secondary)">🎯 ${escapeHtml(ch.learning_goal)}</span>` : ''}
                                ${depsBadge}
                            </div>
                        `;
                    }).join('')}
                    ${order.core_to_learn.length > 5 ? `<div style="font-size:11px;color:var(--text-secondary);padding:4px 12px">还有 ${order.core_to_learn.length - 5} 章...</div>` : ''}
                </div>
            `;
        }

        // 已学章节（简化显示）
        if (order.learned && order.learned.length > 0) {
            html += `
                <div style="margin-bottom:20px">
                    <h3 style="margin:0 0 10px;font-size:14px;color:var(--success)">✅ 已学习 (${order.learned.length}章)</h3>
                    <div style="display:flex;flex-wrap:wrap;gap:4px">
                        ${order.learned.map(ch => `
                            <span style="font-size:11px;padding:3px 8px;background:rgba(0,184,148,0.08);color:var(--success);border-radius:4px">第${ch.idx + 1}章</span>
                        `).join('')}
                    </div>
                </div>
            `;
        }
    }

    // === 第四部分：章节进度总览（精简显示）===
    if (overview.chapter_stats && overview.chapter_stats.length > 0) {
        // 只显示有进度的章节
        const activeChapters = overview.chapter_stats.filter(ch => ch.importance > 0 || ch.mastered > 0);
        
        if (activeChapters.length > 0) {
            html += `
                <div style="margin-bottom:20px">
                    <h3 style="margin:0 0 10px;font-size:14px">📊 重点章节进度</h3>
            `;
            activeChapters.slice(0, 10).forEach(ch => {
                const chPercent = ch.total > 0 ? Math.round(ch.mastered / ch.total * 100) : (ch.importance > 0 ? 0 : 100);
                const importanceStar = ch.importance > 0.7 ? '⭐' : '';
                html += `
                    <div style="display:flex;align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid var(--border);font-size:13px">
                        <span style="min-width:100px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escapeHtml(ch.title || `第${ch.idx + 1}章`)}${importanceStar}</span>
                        <div style="flex:1;height:6px;background:var(--border);border-radius:3px;overflow:hidden">
                            <div style="height:100%;width:${chPercent}%;background:${chPercent === 100 ? 'var(--success)' : 'var(--accent)'};border-radius:3px"></div>
                        </div>
                        <span style="min-width:40px;text-align:right;font-size:11px;color:var(--text-secondary)">${chPercent}%</span>
                    </div>
                `;
            });
            html += '</div>';
        }
    }

    // === 第五部分：学习建议（如有）===
    if (overview.recommendation) {
        html += `
            <div style="padding:16px;background:rgba(108,92,231,0.05);border-radius:12px;border:1px solid rgba(108,92,231,0.15)">
                <h3 style="margin:0 0 8px;font-size:14px;color:var(--accent)">💡 学习建议</h3>
                <p style="margin:0;font-size:13px;line-height:1.6">${escapeHtml(overview.recommendation)}</p>
            </div>
        `;
    }

    container.innerHTML = html;
}

// ==================== 研读模式：思辨笔记弹窗 ====================
function showSpiritualNotesModal() {
    const modal = document.createElement('div');
    modal.className = 'modal-overlay active';
    modal.style.zIndex = '2000';
    modal.onclick = function(e) { if (e.target === modal) modal.remove(); };

    const container = document.createElement('div');
    container.className = 'modal';
    container.style.maxWidth = '800px';
    container.style.maxHeight = '85vh';
    container.style.overflow = 'auto';
    container.innerHTML = '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:20px"><h2>🧠 思辨笔记</h2><button class="btn btn-outline btn-sm" onclick="this.closest(\'.modal-overlay\').remove()">✕</button></div><div id="spiritual-notes-modal-content" style="min-height:200px;text-align:center;color:var(--text-secondary)">加载中...</div>';

    modal.appendChild(container);
    document.body.appendChild(modal);

    loadSpiritualNotesModal();
}

async function loadSpiritualNotesModal() {
    if (!AppState.currentCourseId) return;
    try {
        const data = await API.get(`/api/courses/${AppState.currentCourseId}/spiritual-notes`);
        renderSpiritualNotesModal(data.notes || []);
    } catch (e) {
        console.error('加载思辨笔记失败', e);
        document.getElementById('spiritual-notes-modal-content').innerHTML = '<div style="color:var(--danger)">加载失败</div>';
    }
}

function renderSpiritualNotesModal(notes) {
    const container = document.getElementById('spiritual-notes-modal-content');
    if (!container) return;

    if (!notes || notes.length === 0) {
        container.innerHTML = '<div style="padding:40px;text-align:center;color:var(--text-secondary)">暂无思辨笔记<br><br>研读模式学习结束后会自动生成</div>';
        return;
    }

    let html = '';
    html += '<div style="display:flex;flex-direction:column;gap:16px">';
    notes.forEach(note => {
        html += `
            <div style="background:var(--bg-secondary);border-radius:12px;padding:20px;box-shadow:0 2px 8px rgba(0,0,0,0.05);border-left:4px solid var(--warning)">
                <h3 style="margin-bottom:16px;font-size:16px">📖 ${note.chapter_title || '第' + (note.chapter_index + 1) + '章'}</h3>
        `;

        if (note.core_contradictions && note.core_contradictions.length > 0) {
            html += `
                <div style="margin-bottom:12px">
                    <h4 style="font-size:13px;color:var(--text-secondary);margin-bottom:8px">⚡ 核心矛盾点</h4>
                    <ul style="padding-left:20px;font-size:14px;line-height:1.7">
                        ${note.core_contradictions.map(c => `<li style="margin-bottom:4px">${escapeHtml(c)}</li>`).join('')}
                    </ul>
                </div>
            `;
        }

        if (note.unresolved_questions && note.unresolved_questions.length > 0) {
            html += `
                <div style="margin-bottom:12px">
                    <h4 style="font-size:13px;color:var(--text-secondary);margin-bottom:8px">❓ 未解决问题</h4>
                    <ul style="padding-left:20px;font-size:14px;line-height:1.7">
                        ${note.unresolved_questions.map(q => `<li style="margin-bottom:4px">${escapeHtml(q)}</li>`).join('')}
                    </ul>
                </div>
            `;
        }

        if (note.extension_directions && note.extension_directions.length > 0) {
            html += `
                <div style="margin-bottom:12px">
                    <h4 style="font-size:13px;color:var(--text-secondary);margin-bottom:8px">💡 延伸思考方向</h4>
                    <ul style="padding-left:20px;font-size:14px;line-height:1.7">
                        ${note.extension_directions.map(d => `<li style="margin-bottom:4px">${escapeHtml(d)}</li>`).join('')}
                    </ul>
                </div>
            `;
        }

        if (note.personal_reflection) {
            html += `
                <div>
                    <h4 style="font-size:13px;color:var(--text-secondary);margin-bottom:8px">💭 个人反思</h4>
                    <p style="font-size:14px;line-height:1.7">${escapeHtml(note.personal_reflection)}</p>
                </div>
            `;
        }

        html += '</div>';
    });
    html += '</div>';

    container.innerHTML = html;
}

// ==================== 速读模式：知识快照展示 ====================
async function loadSnapshots() {
    if (!AppState.currentCourseId) return;
    try {
        const data = await API.get(`/api/courses/${AppState.currentCourseId}/snapshots`);
        renderSnapshots(data.snapshots || [], data.highlights);
    } catch (e) {
        console.error('加载知识快照失败', e);
    }
}

function renderSnapshots(snapshots, highlights) {
    const container = document.getElementById('snapshot-container');
    if (!container) return;

    if (!snapshots || snapshots.length === 0) {
        container.innerHTML = '<div style="color:var(--text-secondary);padding:20px;text-align:center">暂无知识快照</div>';
        return;
    }

    let html = '<div class="snapshot-section">';

    // 全局精华
    if (highlights) {
        html += `
            <div class="highlights-card">
                <h3>📊 全书核心知识点</h3>
                <div class="key-points-list">
                    ${(highlights.key_points || []).map((point, i) => `
                        <div class="key-point-item ${i < 3 ? 'priority-high' : 'priority-normal'}">
                            <span class="priority-badge">${i < 3 ? '🔥 核心' : '📌 重要'}</span>
                            <span class="point-desc">${escapeHtml(point)}</span>
                        </div>
                    `).join('')}
                </div>
                ${highlights.chapter_priorities && highlights.chapter_priorities.length > 0 ? `
                    <div class="chapter-priorities">
                        <h4>📚 推荐学习顺序</h4>
                        <ol>${highlights.chapter_priorities.map(p => `<li>${escapeHtml(p)}</li>`).join('')}</ol>
                    </div>
                ` : ''}
                ${highlights.relationships ? `<div class="relationships">${escapeHtml(highlights.relationships)}</div>` : ''}
            </div>
        `;
    }

    // 章节快照
    html += '<h3 style="margin-top:20px">📑 各章知识快照</h3>';
    html += snapshots.map(s => `
        <div class="snapshot-card">
            <div class="snapshot-header">
                <span class="chapter-title">第${s.chapter_index + 1}章</span>
                ${s.core_viewpoint ? `<span class="core-viewpoint">${escapeHtml(s.core_viewpoint)}</span>` : ''}
            </div>
            <div class="snapshot-keywords">
                ${(s.keywords || []).map(k => `<span class="keyword-tag">${escapeHtml(k)}</span>`).join('')}
            </div>
        </div>
    `).join('');

    html += '</div>';
    container.innerHTML = html;
}

// ==================== 研读模式：思辨笔记 ====================
async function loadSpiritualNotes() {
    if (!AppState.currentCourseId) return;
    try {
        const data = await API.get(`/api/courses/${AppState.currentCourseId}/spiritual-notes`);
        renderSpiritualNotes(data.notes || []);
    } catch (e) {
        console.error('加载思辨笔记失败', e);
    }
}

function renderSpiritualNotes(notes) {
    const container = document.getElementById('spiritual-notes-container');
    if (!container) return;

    if (!notes || notes.length === 0) {
        container.innerHTML = '<div style="color:var(--text-secondary);padding:20px;text-align:center">暂无思辨笔记</div>';
        return;
    }

    container.innerHTML = notes.map(note => `
        <div class="spiritual-note-card">
            <h4>🧠 ${note.chapter_title || '未知章节'} - 思辨笔记</h4>
            ${note.core_contradictions && note.core_contradictions.length > 0 ? `
                <div class="note-section">
                    <h5>核心矛盾点</h5>
                    <ul>${note.core_contradictions.map(c => `<li>${escapeHtml(c)}</li>`).join('')}</ul>
                </div>
            ` : ''}
            ${note.unresolved_questions && note.unresolved_questions.length > 0 ? `
                <div class="note-section">
                    <h5>未解决问题</h5>
                    <ul>${note.unresolved_questions.map(q => `<li>${escapeHtml(q)}</li>`).join('')}</ul>
                </div>
            ` : ''}
            ${note.extension_directions && note.extension_directions.length > 0 ? `
                <div class="note-section">
                    <h5>延伸思考方向</h5>
                    <ul>${note.extension_directions.map(d => `<li>${escapeHtml(d)}</li>`).join('')}</ul>
                </div>
            ` : ''}
            ${note.personal_reflection ? `
                <div class="note-section">
                    <h5>个人反思</h5>
                    <p>${escapeHtml(note.personal_reflection)}</p>
                </div>
            ` : ''}
        </div>
    `).join('');
}

// ==================== 知识掌握度仪表盘 ====================
async function loadMasteryDashboard() {
    if (!AppState.currentCourseId) return;
    try {
        const progress = await API.get(`/api/courses/${AppState.currentCourseId}/mastery-progress`);
        renderMasteryDashboard(progress);
    } catch (e) {
        console.error('加载掌握进度失败', e);
    }
}

function renderMasteryDashboard(progress) {
    const container = document.getElementById('mastery-dashboard');
    if (!container) return;

    const total = progress.total || 0;
    const mastered = progress.mastered?.length || 0;
    const inProgress = progress.in_progress?.length || 0;
    const pending = progress.pending?.length || 0;
    const percent = total > 0 ? Math.round(mastered / total * 100) : 0;

    container.innerHTML = `
        <div class="mastery-header">
            <h3>📊 知识掌握进度</h3>
            <span class="mastery-percent">${percent}%</span>
        </div>
        <div class="progress-bar-outer" style="margin:12px 0">
            <div class="progress-bar-inner" style="width:${percent}%"></div>
        </div>
        <div class="mastery-stats">
            <span class="stat mastered">✅ 已掌握 ${mastered}</span>
            <span class="stat in-progress">🔄 进行中 ${inProgress}</span>
            <span class="stat pending">⏳ 待学习 ${pending}</span>
        </div>
        <div id="chapter-breakdown" class="chapter-breakdown"></div>
    `;

    // 章节明细
    const byChapter = progress.by_chapter || {};
    const breakdownContainer = document.getElementById('chapter-breakdown');
    if (breakdownContainer && Object.keys(byChapter).length > 0) {
        let html = '<h4 style="margin-top:16px;font-size:13px;color:var(--text-secondary)">各章掌握情况</h4>';
        Object.entries(byChapter).forEach(([chIdx, stats]) => {
            const chPercent = stats.total > 0 ? Math.round(stats.mastered / stats.total * 100) : 0;
            html += `
                <div class="chapter-progress">
                    <span>第${parseInt(chIdx) + 1}章</span>
                    <div class="progress-bar-mini">
                        <div class="progress-bar-mini-fill" style="width:${chPercent}%"></div>
                    </div>
                    <span>${stats.mastered}/${stats.total}</span>
                </div>
            `;
        });
        breakdownContainer.innerHTML = html;
    }
}

// ==================== 全书总览视图 ====================
async function loadCourseOverview() {
    if (!AppState.currentCourseId) return;
    try {
        const overview = await API.get(`/api/courses/${AppState.currentCourseId}/overview`);
        renderCourseOverview(overview);
    } catch (e) {
        console.error('加载课程概览失败', e);
    }
}

function renderCourseOverview(overview) {
    const container = document.getElementById('course-overview');
    if (!container) return;

    const coverage = overview.knowledge_coverage || { total_points: 0, mastered_points: 0, percent: 0 };
    const percent = coverage.percent || 0;

    // 计算圆环进度
    const circumference = 2 * Math.PI * 45;
    const offset = circumference - (circumference * percent / 100);

    let html = `
        <div class="overview-header">
            <h2>📊 课程学习概览</h2>
            <span class="strategy-badge">${escapeHtml(overview.strategy_description || '')}</span>
        </div>

        <div class="knowledge-coverage">
            <h3>知识点掌握率</h3>
            <div class="coverage-circle">
                <svg viewBox="0 0 100 100">
                    <circle cx="50" cy="50" r="45" fill="none" stroke="#dfe6e9" stroke-width="8"/>
                    <circle cx="50" cy="50" r="45" fill="none"
                            stroke="var(--accent)" stroke-width="8"
                            stroke-dasharray="${circumference}"
                            stroke-dashoffset="${offset}"
                            transform="rotate(-90 50 50)"/>
                </svg>
                <span class="coverage-number">${percent}%</span>
            </div>
            <div class="coverage-stats">
                <span>已掌握 ${coverage.mastered_points}/${coverage.total_points} 个知识点</span>
            </div>
        </div>

        <div class="chapter-stats">
            <h3>章节掌握情况</h3>
            ${(overview.chapter_stats || []).map(ch => {
                const chPercent = ch.total > 0 ? Math.round(ch.mastered / ch.total * 100) : 0;
                return `
                    <div class="chapter-stat-item">
                        <span class="chapter-name">${escapeHtml(ch.title || `第${ch.idx + 1}章`)}</span>
                        <div class="progress-bar-mini">
                            <div class="progress-bar-mini-fill" style="width:${chPercent}%"></div>
                        </div>
                        <span class="chapter-count">${ch.mastered}/${ch.total}</span>
                    </div>
                `;
            }).join('')}
        </div>

        ${overview.weak_areas && overview.weak_areas.length > 0 ? `
            <div class="weak-areas">
                <h3>⚠️ 薄弱环节</h3>
                ${overview.weak_areas.map(w => `
                    <div class="weak-item">
                        <span class="weak-desc">${escapeHtml(w.description)}</span>
                        <span class="weak-chapter">第${w.chapter_index + 1}章</span>
                    </div>
                `).join('')}
            </div>
        ` : ''}

        <div class="recommendation">
            <h3>💡 下一步行动建议</h3>
            <p>${escapeHtml(overview.recommendation || '')}</p>
        </div>
    `;

    container.innerHTML = html;
}

// ==================== 模式-契约联动默认值 ====================
function onReadingModeSelected(mode) {
    const defaults = {
        speed: { depth: 'basic', duration: 15 },
        standard: { depth: 'standard', duration: 30 },
        deep: { depth: 'deep', duration: 60 },
    };

    const modeDefaults = defaults[mode] || defaults.standard;

    // 自动选中对应的深度按钮
    document.querySelectorAll('.depth-btn').forEach(btn => {
        btn.classList.toggle('selected', btn.dataset.depth === modeDefaults.depth);
    });

    // 自动选中对应的时长按钮
    document.querySelectorAll('.duration-btn').forEach(btn => {
        btn.classList.toggle('selected', parseInt(btn.dataset.duration) === modeDefaults.duration);
    });

    // 更新阅读模式选择
    AppState.selectedReadingMode = mode;
    document.querySelectorAll('.mode-btn').forEach(btn => {
        btn.classList.toggle('selected', btn.dataset.mode === mode);
    });

    // 显示预期产出
    const expectedOutputs = {
        speed: '知识快照 + 核心观点列表',
        standard: '知识点掌握报告 + 复习总结',
        deep: '思辨笔记 + 读书笔记',
    };
    showToast(`预期产出：${expectedOutputs[mode]}`, 'info');
}

// ==================== 辩证分析标注 ====================
function renderDialecticalContent(content, state) {
    // 检测是否包含辩证分析标记
    const dialecticalMarkers = {
        'cross_chapter': '🔄 跨章对比',
        'hidden_assumption': '🔍 隐含前提',
        'counter_argument': '⚔️ 反方观点',
        'application_test': '🧪 应用场景检验',
    };

    // 在消息气泡旁显示分析类型标签
    for (const [key, label] of Object.entries(dialecticalMarkers)) {
        if (content.includes(key)) {
            return `<span class="dialectical-tag">${label}</span>` + content;
        }
    }
    return content;
}

// ==================== 加载课程详情（兼容函数） ====================
async function loadCourseDetail(courseId) {
    await openCourse(courseId);
}


