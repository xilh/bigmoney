/**
 * BigBill — 全局 JavaScript
 * Toast 通知 · 数字动画 · 通用工具
 */

// ==================== Toast 通知 ====================
function showToast(message, type = 'info') {
    const container = document.getElementById('toastContainer');
    if (!container) return;

    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;

    const icon = type === 'success' ? '✅' : type === 'error' ? '❌' : 'ℹ️';
    toast.innerHTML = `<span>${icon}</span><span>${message}</span>`;

    container.appendChild(toast);

    // Auto remove after 3.5s
    setTimeout(() => {
        if (toast.parentNode) toast.parentNode.removeChild(toast);
    }, 3500);
}

// ==================== 数字动画 ====================
function animateValue(element, start, end, duration) {
    if (start === end) return;
    const range = end - start;
    const startTime = performance.now();

    function update(currentTime) {
        const elapsed = currentTime - startTime;
        const progress = Math.min(elapsed / duration, 1);
        // Ease out cubic
        const eased = 1 - Math.pow(1 - progress, 3);
        const current = Math.floor(start + range * eased);
        element.textContent = '¥' + current.toLocaleString();
        if (progress < 1) requestAnimationFrame(update);
    }

    requestAnimationFrame(update);
}

// Auto-animate elements with data-count attribute
document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('[data-count]').forEach(el => {
        const target = parseFloat(el.dataset.count);
        if (target > 0) {
            animateValue(el, 0, target, 1200);
        }
    });
});

// ==================== CSRF Token Helper ====================
function getCsrfToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    if (meta) return meta.content;

    const cookie = document.cookie.split(';')
        .find(c => c.trim().startsWith('csrftoken='));
    if (cookie) return cookie.split('=')[1];

    return '';
}

// ==================== Format Helpers ====================
function formatCurrency(value) {
    if (value === null || value === undefined) return '-';
    const num = Number(value);
    if (num >= 100000000) return '¥' + (num / 100000000).toFixed(2) + '亿';
    if (num >= 10000) return '¥' + (num / 10000).toFixed(2) + '万';
    return '¥' + num.toLocaleString('zh-CN', { minimumFractionDigits: 0, maximumFractionDigits: 0 });
}

function formatPercent(value) {
    if (value === null || value === undefined) return '-';
    const num = Number(value);
    return (num >= 0 ? '+' : '') + num.toFixed(2) + '%';
}

// ==================== Keyboard Shortcut ====================
document.addEventListener('keydown', function (e) {
    // ESC closes modals
    if (e.key === 'Escape') {
        document.querySelectorAll('.modal-overlay.active').forEach(m => {
            m.classList.remove('active');
        });
    }
});

// ==================== Click outside modal to close ====================
document.addEventListener('click', function (e) {
    if (e.target.classList.contains('modal-overlay')) {
        e.target.classList.remove('active');
    }
});
