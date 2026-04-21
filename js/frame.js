const divider = document.querySelector('#divider');
const viewport = document.querySelector('#viewport');
const window1 = document.querySelector('#window1');
const window1_iframe = document.querySelector('#window1_if');
const window2 = document.querySelector('#window2');
const window2_iframe = document.querySelector('#window2_if');
const landscape_frame_style = document.querySelector('#landscape_frame_style');
const navigation = document.querySelector('#navigation');
const iframe_container_bg1 = document.querySelector('#iframe_container_bg1');
const iframe_container_bg2 = document.querySelector('#iframe_container_bg2');

const rem_in_px = parseFloat(getComputedStyle(document.documentElement).fontSize);
const min_window_width = 3 * rem_in_px; // Minimum width for each window in pixels
const min_active_window_width = 15 * rem_in_px; // Minimum width for active window to be interactable

let window1_percent = parseFloat(localStorage.getItem('mt_window1_percent')) || 0.3;
window1_percent = Math.min(Math.max(window1_percent, 0.05), 0.95);

let divider_mouse_down = false;
function resize_windows(e) {
    const viewport_rect = viewport.getBoundingClientRect();
    const new_width = e.clientX - viewport_rect.left;
    window1_percent = new_width / viewport_rect.width;
    if (new_width > min_window_width && new_width < viewport_rect.width - min_window_width) {
        window1.style.width = `${new_width}px`;
        window2.style.width = `${viewport_rect.width - new_width}px`;
    }
}


function sync_window_visibility() {
    if (window1.getBoundingClientRect().width < min_active_window_width) {
        window1_iframe.style.opacity = 0.3;
        window1_iframe.style.pointerEvents = "none";
        iframe_container_bg1.style.backgroundImage = `url('${url_base}/img/disable.svg')`;
    } else {
        window1_iframe.style.opacity = 1;
        window1_iframe.style.pointerEvents = "auto";
        iframe_container_bg1.style.backgroundImage = `url('${url_base}/img/stars.svg')`;
    }
    if (window2.getBoundingClientRect().width < min_active_window_width) {
        window2_iframe.style.opacity = 0.3;
        window2_iframe.style.pointerEvents = "none";
        iframe_container_bg2.style.backgroundImage = `url('${url_base}/img/disable.svg')`;
    } else {
        window2_iframe.style.opacity = 1;
        window2_iframe.style.pointerEvents = "auto";
        iframe_container_bg2.style.backgroundImage = `url('${url_base}/img/stars.svg')`;
    }
}

function stop_divider_drag(e) {
    resize_windows(e);
    divider.style.width = "0.8rem";
    divider.style.left = window1.style.width;
    // window1_percent = new_width / viewport_rect.width;
    divider_mouse_down = false;
    sync_window_visibility();
}


function body_resize() {
    window1_percent = Math.min(Math.max(window1_percent, 0.05), 0.95);
    new_is_landscape = get_viewport_aspect_ratio() > 1;
    landscape_toggled = new_is_landscape != is_landscape;
    is_landscape = new_is_landscape;
    if (is_landscape) {
        landscape_frame_style.disabled = false;
        if (landscape_toggled) {
            window2.style.display = "block";
            divider.style.display = "block";
        }
        const viewport_rect = viewport.getBoundingClientRect();
        const new_width = viewport_rect.width * window1_percent;
        window1.style.width = `${new_width}px`;
        window2.style.width = `${viewport_rect.width - new_width}px`;
        divider.style.left = window1.style.width;
        document.querySelectorAll('.close_window_btn').forEach(btn => {
            btn.style.display = "block";
        });
    } else {
        landscape_frame_style.disabled = true;
        window1.style.width = "100%";
        window2.style.display = "none";
        divider.style.display = "none";
        // hide all .close_window_btns
        document.querySelectorAll('.close_window_btn').forEach(btn => {
            btn.style.display = "none";
        });
    }
    sync_window_visibility();
    if (landscape_toggled) {
        console.log(`Landscape mode toggled: ${is_landscape}`);
        setTimeout(() => {
            // fix divider position after toggle, as it may be incorrect due to transition and resize events firing in unpredictable order
            if (is_landscape) {
                const viewport_rect = viewport.getBoundingClientRect();
                const new_width = viewport_rect.right * window1_percent;
                window1.style.width = `${new_width}px`;
                window2.style.width = `${viewport_rect.width - new_width}px`;
                divider.style.left = window1.style.width;
            }
        }, 100);
    }
}

document.addEventListener('DOMContentLoaded', () => {
    body_resize();
});

window.addEventListener('resize', () => {
    body_resize();
});

divider.addEventListener('pointerdown', (e) => {
    divider_mouse_down = true;
    divider.style.left = 0;
    divider.style.width = "100%";
});

divider.addEventListener('pointerup', (e) => {
    stop_divider_drag(e);
});
divider.addEventListener('pointerleave', (e) => {
    if (divider_mouse_down) {
        stop_divider_drag(e);
    }
});

window.addEventListener('pointermove', (e) => {
    if (divider_mouse_down) {
        resize_windows(e);
        sync_window_visibility();
    }
});

// get user viewport aspect ratio
function get_viewport_aspect_ratio() {
    return window.innerWidth / window.innerHeight;
}

is_landscape = get_viewport_aspect_ratio() > 1;

function show_content(url, window) {
    if (event)
        event.preventDefault();
    if (!is_landscape) {
        window = 1;
    }
    if (window1.getBoundingClientRect().width < min_active_window_width) {
        window = 2;
    }
    if (window2.getBoundingClientRect().width < min_active_window_width) {
        window = 1;
    }
    console.log(`Loading ${url} in window ${window}`);
    if (window === 1) {
        window1_iframe.src = url;
        // focus the iframe
        window1_iframe.focus();
    } else {
        window2_iframe.src = url;
        window2_iframe.focus();
    }
    return true;
}

function close_window(window) {
    if (window === 1) {
        window1_iframe.src = "about:blank";
    } else {
        window2_iframe.src = "about:blank";
    }
}

function hide_navigation() {
    if (!is_landscape) {
        navigation.style.display = "none";
    }
}

function show_navigation() {
    navigation.style.display = "inline-block";
}

function enter_fullscreen() {
    if (document.fullscreenElement) {
        document.exitFullscreen();
    } else {
        document.documentElement.requestFullscreen();
    }
}

function go_tl() {
    page = localStorage.getItem("tl_current_page") || 1;
    sort_type = localStorage.getItem("tl_current_sort_type") || "new";
    tab = localStorage.getItem("tl_current_tab") || "posts";
    url = `${url_base}/tl?tab=${tab}&sort_type=${sort_type}&p=${page}`;
    show_content(url, 2);
}

function go_userlist(){
    page = localStorage.getItem("ul_current_page") || 1;
    sort_type = localStorage.getItem("ul_current_sort_type") || "new";
    url = `${url_base}/userlist?sort_type=${sort_type}&p=${page}`;
    show_content(url, 1);
}

// handles reloading, saveing, and restoring iframe state
// save iframe current location and scroll position before unload
window.addEventListener('beforeunload', function () {
    localStorage.setItem('mt_window1_percent', window1_percent);
    // window1
    const iframeWindow1 = window1_iframe.contentWindow;
    const iframeSrc1 = iframeWindow1.location.href;
    if (iframeSrc1.includes("shorts") || iframeSrc1.includes("ruffle") || iframeSrc1.includes("login")) {
        // do not save those positions, as they may cause issues when restored
        return;
    }
    const scrollPosition1 = iframeWindow1.scrollY || iframeWindow1.pageYOffset;
    localStorage.setItem('mt_iframeSrc1', iframeSrc1);
    localStorage.setItem('mt_iframeScrollPosition1', scrollPosition1);
    localStorage.setItem('mt_iframeTimestamp1', Date.now());
    // window2
    const iframeWindow2 = window2_iframe.contentWindow;
    const iframeSrc2 = iframeWindow2.location.href;
    if (iframeSrc2.includes("shorts") || iframeSrc2.includes("ruffle") || iframeSrc2.includes("login")) {
        // do not save those positions, as they may cause issues when restored
        return;
    }
    const scrollPosition2 = iframeWindow2.scrollY || iframeWindow2.pageYOffset;
    localStorage.setItem('mt_iframeSrc2', iframeSrc2);
    localStorage.setItem('mt_iframeScrollPosition2', scrollPosition2);
    localStorage.setItem('mt_iframeTimestamp2', Date.now());

});

// restore iframe src and scroll position on load
window.addEventListener('load', function () {
    // window1
    const savedSrc1 = localStorage.getItem('mt_iframeSrc1');
    const savedScrollPosition1 = localStorage.getItem('mt_iframeScrollPosition1');
    const savedTimestamp1 = localStorage.getItem('mt_iframeTimestamp1');
    const savedWindow1Percent = localStorage.getItem('mt_window1_percent');
    const now = Date.now();
    if (savedTimestamp1 && now - savedTimestamp1 > 20 * 1000) {
        localStorage.removeItem('mt_iframeSrc1');
        localStorage.removeItem('mt_iframeScrollPosition1');
        localStorage.removeItem('mt_iframeTimestamp1');
        localStorage.removeItem('mt_window1_percent');
        window1_iframe.src = url_base + "/userlist";
        return;
    }
    if (savedSrc1) {
        window1_iframe.src = savedSrc1;
        window1_iframe.onload = function () {
            window1_iframe.contentWindow.scrollTo(0, parseInt(savedScrollPosition1) || 0);
            // clear onload after restoring scroll position to prevent unwanted scroll on next load
            window1_iframe.onload = null;
        }
    } else {
        window1_iframe.src = url_base + "/userlist";
    }
    // window2
    const savedSrc2 = localStorage.getItem('mt_iframeSrc2');
    const savedScrollPosition2 = localStorage.getItem('mt_iframeScrollPosition2');
    const savedTimestamp2 = localStorage.getItem('mt_iframeTimestamp2');
    if (savedTimestamp2 && now - savedTimestamp2 > 20 * 1000) {
        localStorage.removeItem('mt_iframeSrc2');
        localStorage.removeItem('mt_iframeScrollPosition2');
        localStorage.removeItem('mt_iframeTimestamp2');
        return;
    }
    if (savedSrc2) {
        window2_iframe.src = savedSrc2;
        window2_iframe.onload = function () {
            window2_iframe.contentWindow.scrollTo(0, parseInt(savedScrollPosition2) || 0);
            // clear onload after restoring scroll position to prevent unwanted scroll on next load
            window2_iframe.onload = null;
        }
    }
    // window1_percent = parseFloat(savedWindow1Percent) || 0.3;
    // body_resize();
});

[window1, window2].forEach((win, index) => {
    win.addEventListener('dragenter', (e) => {
        e.preventDefault();
        win.classList.add('drag_over');
    });
    win.addEventListener("dragover", (e) => {
        e.preventDefault();
    });
    win.addEventListener('dragleave', (e) => {
        e.preventDefault();
        win.classList.remove('drag_over');
    });
});

window1.addEventListener('drop', (e) => {
    window1.classList.remove('drag_over');
    console.log(`Dropped on window 1`);
    const url = e.dataTransfer.getData('text/plain');
    // alert(`Dropped URL: ${url} on window 1`);
    window1_iframe.src = url;
    e.preventDefault();
});

window2.addEventListener('drop', (e) => {
    window2.classList.remove('drag_over');
    console.log(`Dropped on window 2`);
    const url = e.dataTransfer.getData('text/plain');
    // alert(`Dropped URL: ${url} on window 2`);
    window2_iframe.src = url;
    e.preventDefault();
});

document.querySelectorAll('.nav_item').forEach(item => {
    // drag start listener
    item.addEventListener('dragstart', (e) => {
        e.dataTransfer.setData('text/plain', item.getAttribute('href'));
        console.log(`Started dragging ${item.getAttribute('href')}`);
        window1_iframe.style.pointerEvents = "none";
        window2_iframe.style.pointerEvents = "none";
    });
    //drag stop listener
    item.addEventListener('dragend', (e) => {
        console.log(`Stopped dragging ${item.getAttribute('href')}`);
        body_resize();
    });

});