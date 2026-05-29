const search_bar_input = document.getElementById("search_bar_input");
const landscape_style = document.getElementById("landscape_style");
const overlay = document.getElementById("overlay");
const alt_text = document.getElementById("alt_text");
const alt_text_text = document.getElementById("alt_text_text");
const toast_div = document.getElementById("toast_div");
const container = document.getElementById("container");
const container_outer = document.getElementById("container_outer");
const parent_iframe = window.frameElement;
const page_text = document.getElementById("page_text");


var touch_start_x = 0;
var touch_end_x = 0;
var touch_start_y = 0;
var touch_end_y = 0;

var current_pager_page = 0;
var halt_next_page_load = false;

var overlay_on = false;
var toast_timeout = null;

function toast(message, onclick_href = "#") {
    if (!message) return;
    console.log("[TOAST] " + message)
    toast_div.innerText = message;
    // toast_div.style.pointerEvents = "all";
    toast_div.style.opacity = 1;
    clearTimeout(toast_timeout);
    toast_timeout = setTimeout(() => {
        toast_div.style.pointerEvents = "none";
        toast_div.style.opacity = 0;
    }, 5300)
}

function toggle_value(container_id, value_name) {
    toggle_container = document.getElementById(container_id);
    value = localStorage.getItem(value_name, "true") == "true";
    value = !value;
    localStorage.setItem(value_name, (value).toString());
    if (value) {
        toggle_container.setAttribute("data-enabled", "true");
    }
    else {
        toggle_container.setAttribute("data-enabled", "false");
    }
    toggle_btn = toggle_container.querySelector(".menu_toggle");
    if (toggle_btn) {
        if (value) {
            toggle_btn.classList.add("menu_toggle_on");
        }
        else {
            toggle_btn.classList.remove("menu_toggle_on");
        }
    }
}

function init_toggle(container_id, value_name) {
    toggle_container = document.getElementById(container_id);
    value = localStorage.getItem(value_name, "true") == "true";
    if (value) {
        toggle_container.setAttribute("data-enabled", "true");
    }
    else {
        toggle_container.setAttribute("data-enabled", "false");
    }
    toggle_btn = toggle_container.querySelector(".menu_toggle");
    if (toggle_btn) {
        if (value) {
            toggle_btn.classList.add("menu_toggle_on");
        }
        else {
            toggle_btn.classList.remove("menu_toggle_on");
        }
    }
}

function get_bool(value_name) {
    return localStorage.getItem(value_name, "true") == "true";
}

function write2clipboard(text) {
    if (!navigator.clipboard) {
        alert('Clipboard API not supported, https is required, or permission denied.');
        return;
    }
    navigator.clipboard.writeText(text)
        .then(() => {
            toast('Copied to clipboard');
        })
        .catch(err => {
            console.error('Failed to write to clipboard: ', err);
        });
}

function focusHighlighted() {
    highlighted = document.querySelector('.card_highlighted');
    if (highlighted) {
        // check location.hash
        if (location.hash == "#tags") {
            // scroll to .card_body_tags
            tags = highlighted.querySelector('.card_body_tags');
            tags.scrollIntoView({ behavior: 'smooth', block: 'center' });
            tags.classList.add('highlight');
        }
        else {
            card_body_text = highlighted.querySelector('.card_body_text');
            card_body_text.scrollIntoView({ behavior: 'instant', block: 'center' });
            // highlighted.classList.add('highlight');
        }
    }
    else {
        console.log("No highlighted card found");
    }
}


function show_loading_icon() {
    const loading_icon = parent.document.getElementById('loading_icon');
    if (loading_icon) {
        loading_icon.style.display = 'block';
    }
}

function hide_loading_icon() {
    const loading_icon = parent.document.getElementById('loading_icon');
    if (loading_icon) {
        loading_icon.style.display = 'none';
    }
}

function toggle_user_context_menu() {
    const menu_warpper = document.getElementById("user_context_menu_warpper");
    if (menu_warpper.style.display === "block") {
        menu_warpper.style.display = "none";
    } else {
        menu_warpper.style.display = "block";
    }
}

function hide_user_context_menu() {
    menu_warpper = document.getElementById("user_context_menu_warpper");
    if (menu_warpper) {
        menu_warpper.style.display = "none";
    }
}

function flag_user(uid) {
    fetch(url_base + "/api/flag_user?uid=" + uid, {
        method: 'GET'
    }).then(response => response.json())
        .then(data => {
            console.log(data);
            // alert(data['message']);
            location.reload();
        })
}

function unflag_user(uid) {
    fetch(url_base + "/api/unflag_user?uid=" + uid, {
        method: 'GET'
    }).then(response => response.json())
        .then(data => {
            console.log(data);
            // alert(data['message']);
            location.reload();
        })
}

function select_user(uid, tab) {
    const toggle = document.getElementById("t" + uid);
    const group_btn = document.getElementById("group_btn");
    if (toggle.classList.contains("user_select_toggle_active")) {
        toggle.classList.remove("user_select_toggle_active");
    }
    else {
        toggle.classList.add("user_select_toggle_active");
        group_btn.style.display = "block";
    }

    select_users = document.querySelectorAll(".user_select_toggle_active");
    if (tab == "groups") {
        group_btn.innerText = "Remove from group (" + select_users.length + ")";
    }
    else {
        group_btn.innerText = "Add to group (" + select_users.length + ")";
    }
    if (select_users.length == 0) {
        group_btn.style.display = "none";
    }
    // prevent event from bubbling up to user card
    event.stopPropagation();
}

function group_users() {
    const selected_users = document.querySelectorAll(".user_select_toggle_active");
    if (selected_users.length == 0) {
        alert("Please select at least one user");
        return;
    }
    group_name = prompt("Group name", selected_users[0].id.substring(1) + " and " + (selected_users.length - 1) + " more");
    if (!group_name) {
        return;
    }
    const uids = Array.from(selected_users).map(toggle => toggle.id.substring(1));
    url = url_base + "/api/group_users";

    fetch(url, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({
            group_name: group_name,
            uids: uids
        })
    }).then(response => response.json())
        .then(data => {
            console.log(data);
            location.href = url_base + "/userlist?tab=groups";
        });
}

function ungroup_user() {
    const selected_users = document.querySelectorAll(".user_select_toggle_active");
    if (selected_users.length == 0) {
        alert("Please select at least one user");
        return;
    }
    pairs = Array.from(selected_users).map(toggle => {
        group_id = toggle.getAttribute("data-group");
        uid = toggle.id.substring(1);
        return {
            group_id: group_id,
            uid: uid
        };
    });
    url = url_base + "/api/ungroup_users";
    fetch(url, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({
            pairs: pairs
        })
    }).then(response => response.json())
        .then(data => {
            console.log(data);
            location.reload();
        });
}

function ungroup(group_id) {
    if (!group_id) {
        return;
    }
    fetch(url_base + "/api/ungroup", {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({
            group_id: group_id
        })
    }).then(response => response.json())
        .then(data => {
            console.log(data);
            location.reload();
        });
}

function rename_group(group_name) {
    if (!group_name) {
        return;
    }
    const new_group_name = prompt("New group name", group_name);
    if (!new_group_name) {
        return;
    }
    fetch(url_base + "/api/rename_group", {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({
            old_group_name: group_name,
            new_group_name: new_group_name
        })
    }).then(response => response.json())
        .then(data => {
            console.log(data);
            location.reload();
        });
}

function show_alt(id) {
    const alt_div = document.getElementById(id);
    const alt_text_data = alt_div.getAttribute("data");
    // alert(alt_text_data);
    alt_text_text.innerText = alt_text_data;
    alt_text.style.display = "block";
    location.hash = "alt_text";
    document.body.style.overflowY = 'hidden';
    event.preventDefault();
    event.stopPropagation();
}

function hide_alt() {
    alt_text.style.display = "none";
    history.back();
}

function add_rescan(url) {
    add_job(url, 0, 0, 1)
}

function add_rebuild(url) {
    // confirm dialog
    if (!confirm("Are you sure you want to rebuild this user? This will delete all existing data in database and rescan from scratch, files will not be deleted.")) {
        return;
    }
    add_job(url, 0, 0, 2)
}

function show_rename_dialog() {
    toggle_user_context_menu();
    rename_dialog = document.getElementById("rename_dialog");
    rename_dialog.style.display = "block";
    document.body.style.overflowY = 'hidden';
}

function hide_rename_dialog() {
    rename_dialog = document.getElementById("rename_dialog");
    rename_dialog.style.display = "none";
}

function confirm_rename() {
    old_uid = document.getElementById("old_username").innerText;
    type_ = old_uid.split("@")[1];
    new_username = document.getElementById("new_username").value;
    if (!new_username) {
        alert("Please enter new username");
        return;
    }
    if (new_username.includes(" ")) {
        alert("Username cannot contain spaces");
        return;
    }
    new_uid = new_username + "@" + type_;
    if (old_uid == new_uid) {
        alert("Old and new username cannot be the same");
        return;
    }
    fetch(url_base + "/api/rename_user", {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({
            old_uid: old_uid,
            new_uid: new_uid
        })
    }).then(response => response.json())
        .then(data => {
            console.log(data);
            if (data.status === "ok") {
                alert("User renamed successfully");
                location.href = url_base + "/user/" + type_ + "/" + new_username;
            }
            else {
                alert("Failed to rename user: " + data.message);
            }
        });
}

function paste_from_clipboard() {
    if (!navigator.clipboard) {
        alert('Clipboard API not supported, https is required, or permission denied.');
        return;
    }
    navigator.clipboard.readText()
        .then(text => {
            document.getElementById('url_input').value = text;
            url_input();
        })
        .catch(err => {
            console.error('Failed to read clipboard contents: ', err);
        });
}

function interrupt_download() {
    fetch(url_base + '/api/interrupt', {
        method: 'GET',
    }).then(response => response.json())
        .then(data => {
            toast('Interrupting download...');
        })
}

function add_job(url, full, media_only, rescan = 0) {
    queue_div = document.getElementById('queue');
    current_div = document.getElementById('current');
    var jsonData = {
        'url': url,
        'full': full,
        'media_only': media_only,
        'rescan': rescan
    }
    fetch(url_base + '/api/download', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify(jsonData),
    }).then(response => response.json())
        .then(data => {
            toast(data.message);
            if (current_div) {
                current_div.innerHTML = '';
                if (data.current.length > 0) {
                    data.current.forEach(function (item) {
                        html_frag = '<div class=\'current_job\'>'
                        if (item.full) {
                            html_frag += '<img src=\'' + url_base + '/img/select.svg\' class=\'queue_job_icon\'>';
                        }
                        if (item.media_only) {
                            html_frag += '<img src=\'' + url_base + '/img/media.svg\' class=\'queue_job_icon\'>';
                        }
                        html_frag += item.url + '</div>';
                        current_div.innerHTML += html_frag;
                    });
                }
            }
            if (queue_div) {
                queue_div.innerHTML = '';
                if (data.queue) {
                    data.queue.forEach(function (item, index) {
                        console.log("Queue item", index, item);
                        html_frag = '<div class=\'job\'>'
                        if (item[1]) {
                            html_frag += '<img src=\'' + url_base + '/img/select.svg\' class=\'queue_job_icon\'>';
                        }
                        if (item[2]) {
                            html_frag += '<img src=\'' + url_base + '/img/media.svg\' class=\'queue_job_icon\'>';
                        }
                        html_frag += item[0] + '</div>';
                        queue_div.innerHTML += html_frag;
                    });
                }
            }
        });
    hide_user_context_menu();
}

function url_input() {
    url = document.getElementById('url_input').value;
    full = localStorage.getItem("dl_full") === "true";
    media_only = localStorage.getItem("dl_media") === "true";
    console.log("Adding job with url:", url, "full:", full, "media_only:", media_only);
    add_job(url, full, media_only);
    document.getElementById('url_input').value = "";
}

function upload_cookies(type_name) {
    const file_input = document.createElement('input');
    file_input.type = 'file';
    file_input.accept = '.txt';
    file_input.onchange = e => {
        const file = e.target.files[0];
        const formData = new FormData();
        formData.append('cookies', file);
        formData.append('type', type_name);
        fetch(url_base + '/api/upload_cookies', {
            method: 'POST',
            body: formData
        }).then(response => response.json())
            .then(data => {
                toast(data.message);
            });
    }
    file_input.click();
}



function enter_fullscreen() {
    if (parent.document.fullscreenElement) {
        parent.document.exitFullscreen();
    }
    else {
        parent.document.documentElement.requestFullscreen();
    }
}


function iframe_enter_fullscreen() {
    if (parent_iframe) {
        if (parent.document.fullscreenElement == parent_iframe) {
            parent.document.exitFullscreen();
        }
        else {
            parent_iframe.requestFullscreen();
        }
    }

}

function show_search_history() {
    const search_float = document.getElementById("search_float");
    const search_history_list = document.getElementById("search_history_list");
    search_float.style.display = "block";
    search_float.style.opacity = 1;
    //load search history from localstorage
    history_list = JSON.parse(localStorage.getItem("search_history")) || ["@fa", "@reddit", "@x", "@bsky", "@patreon"];
    search_history_list.innerHTML = "";
    history_list.forEach(item => {
        const div = document.createElement("div");
        item = decodeURIComponent(item);
        div.className = "search_history_item";
        div.innerText = item;
        div.onclick = () => {
            search_bar_input.value = item;
            search();
        };
        search_history_list.appendChild(div);
    });
}

function add_search_history_item(item) {
    history_list = JSON.parse(localStorage.getItem("search_history")) || ["@fa", "@reddit", "@x", "@bsky", "@patreon"];
    //add to history if not exists
    item = decodeURIComponent(item);
    if (!history_list.includes(item)) {
        history_list.unshift(item);
        //keep only 50 items
        history_list = history_list.slice(0, 50);
        localStorage.setItem("search_history", JSON.stringify(history_list));
    }
}

function hide_search_history() {
    const search_float = document.getElementById("search_float");
    search_float.style.opacity = 0;
    setTimeout(() => {
        search_float.style.display = "none";
    }, 200);
}

function clear_search_history() {
    localStorage.removeItem("search_history");
    show_search_history();
}


var viewer_video_pause = false;

function toggle_video_play() {
    if (viewer_video.paused) {
        viewer_video.play();
        viewer_video_pause = false;
        pause_btn_img.src = url_base + "/img/pause_w.svg";
        pause_btn_img.style.marginLeft = "0.8rem";
    } else {
        viewer_video.pause();
        viewer_video_pause = true;
        pause_btn_img.src = url_base + "/img/play_w.svg";
        pause_btn_img.style.marginLeft = "0.9rem";
    }
}


function hide_overlay() {
    overlay_on = false;
    if (parent.document.fullscreenElement == parent_iframe) {
        parent.document.exitFullscreen();
    };
    show_navigation();
}

function init_viewer_video_controls() {
    console.log("init video controls");
    if (!viewer_videoseek) {
        console.log("no video seek");
        return;
    }
    viewer_video.addEventListener('loadedmetadata', function () {
        viewer_videoseek.max = viewer_video.duration;
        time_total.innerHTML = new Date(viewer_video.duration * 1000).toISOString().substr(14, 5);
    });
    viewer_video.addEventListener('timeupdate', function () {
        sync_viewer_videotime();
    });
    viewer_videoseek.addEventListener('input', function () {
        viewer_video.currentTime = viewer_videoseek.value;
    });
    //wheel to seek
    viewer_video.addEventListener('wheel', function (e) {
        deltaY = e.deltaY;
        if (deltaY > 0) {
            viewer_video.currentTime += 2;
        }
        else {
            viewer_video.currentTime -= 2;
        }
    });

    let time_when_pointer_down = 0;
    viewer_container_video.addEventListener('pointerdown', function (e) {
        is_pointer_down = true;
        down_x = e.clientX;
        down_y = e.clientY;
        time_when_pointer_down = viewer_video.currentTime;
    });
    viewer_container_video.addEventListener('pointermove', function (e) {
        if (is_pointer_down) {
            viewer_video.pause()
            move_x = e.clientX - down_x;
            move_y = e.clientY - down_y;
            if (Math.abs(move_y) > 10 && Math.abs(move_y) > Math.abs(move_x)) {
                return;
            }
            if (Math.abs(move_x) > 10) {
                viewer_video.currentTime = time_when_pointer_down + move_x / 50;
            }
            sync_viewer_videotime();
        }
    });
    viewer_container_video.addEventListener('touchmove', function (e) {
        if (is_pointer_down) {
            viewer_video.pause()
            move_x = e.touches[0].clientX - down_x;
            move_y = e.touches[0].clientY - down_y;
            if (Math.abs(move_y) > 10 && Math.abs(move_y) > Math.abs(move_x)) {
                return;
            }
            if (Math.abs(move_x) > 10) {
                viewer_video.currentTime = time_when_pointer_down + move_x / 50;
            }
            sync_viewer_videotime();
        }
    });
    viewer_container_video.addEventListener('pointerup', function (e) {
        is_pointer_down = false;
        swipe_to_next_prev(e, allow_swipe_back = true, allow_next_prev = false);
        up_x = e.clientX;
        up_y = e.clientY;
        if (Math.abs(up_x - down_x) < 5 && Math.abs(up_y - down_y) < 5) {
            show_card_wth_timeout();
        }
        if (!viewer_video_pause)
            viewer_video.play();
        viewer_video_pause = false;
    });
    viewer_container_video.addEventListener('pointerleave', function (e) {
        is_pointer_down = false;
        if (!viewer_video_pause && overlay_on)
            viewer_video.play();
        viewer_video_pause = false;
    });
}


var media_x = 0;
var media_y = 0;
var down_x = 0;
var down_y = 0;
var media_scale = 1;
var touch_distance = 0;
var is_pointer_down = false;
var transform_reset_debounce = false;

function init_img_controls() {
    console.log("init image controls");
    viewer_container_img.addEventListener('wheel', function (e) {
        deltaY = e.deltaY;
        if (deltaY > 0) {
            //todo zoom out
            media_scale = Math.max(1, media_scale * 0.8);
        }
        else {
            //todo zoom in
            media_scale = Math.min(8, media_scale * 1.2);
        }
        if (media_scale == 1) {
            media_x = 0;
            media_y = 0;
        };
        if (media_scale > 1) {
            reset_transform_btn.style.display = "block";
        }
        else {
            reset_transform_btn.style.display = "none";
        };
        viewer_img.style.transform = `scale(${media_scale}) translate(${media_x}px, ${media_y}px)`;
    });
    viewer_container_img.addEventListener('pointerdown', function (e) {
        if (transform_reset_debounce) {
            return;
        }
        is_pointer_down = true;
        down_x = e.clientX;
        down_y = e.clientY;
        // console.log("pointer down");
    });
    viewer_container_img.addEventListener('pointermove', function (e) {
        if (media_scale == 1) {
            return;
        }
        if (is_pointer_down) {
            media_x += e.movementX / media_scale;
            media_y += e.movementY / media_scale;
            viewer_img.style.transform = `scale(${media_scale}) translate(${media_x}px, ${media_y}px)`;
        }
    });
    viewer_container_img.addEventListener('touchmove', function (e) {
        if (e.touches.length == 2) {
            //pinch zoom
            var touch1 = e.touches[0];
            var touch2 = e.touches[1];
            var current_distance = Math.sqrt(Math.pow(touch2.clientX - touch1.clientX, 2) + Math.pow(touch2.clientY - touch1.clientY, 2));
            if (touch_distance) {
                if (current_distance > touch_distance) {
                    media_scale = Math.min(8, media_scale * (current_distance / touch_distance));
                }
                else {
                    media_scale = Math.max(0.3, media_scale * (current_distance / touch_distance));
                }
                viewer_img.style.transform = `scale(${media_scale}) translate(${media_x}px, ${media_y}px)`;
            }
            touch_distance = current_distance;
        }
    });
    viewer_container_img.addEventListener('pointerup', function (e) {
        is_pointer_down = false;
        touch_distance = 0;
        // console.log("pointer up");
        if (media_scale == 1 && !transform_reset_debounce) {
            swipe_to_next_prev(e, allow_swipe_back = true, allow_next_prev = true);
        }
        if (media_scale < 1) {
            reset_media_transform();
        }
        else if (media_scale > 1) {
            document.getElementById('reset_transform_btn').style.display = "block";
        }
        up_x = e.clientX;
        up_y = e.clientY;
        if (Math.abs(up_x - down_x) < 5 && Math.abs(up_y - down_y) < 5) {
            show_card_wth_timeout();
        }
    });
    viewer_container_img.addEventListener('pointerleave', function (e) {
        is_pointer_down = false;
        touch_distance = 0;
        if (media_scale < 1) {
            reset_media_transform();
        }
        else if (media_scale > 1) {
            document.getElementById('reset_transform_btn').style.display = "block";
        }
    });
}

function swipe_to_next_prev(e, allow_swipe_back = false, allow_next_prev = true) {
    up_x = e.clientX;
    up_y = e.clientY;
    alpha_x = up_x - down_x;
    alpha_y = up_y - down_y;
    // console.log(down_x, down_y)
    // console.log(alpha_x, alpha_y)
    threshold = 60;
    if (((Math.abs(alpha_x) + 1) / (Math.abs(alpha_y) + 1)) > 1.5) {
        if (alpha_x > threshold && allow_next_prev) {
            media_prev_btn.click();
        }
        else if (alpha_x < -threshold && allow_next_prev) {
            media_next_btn.click();
        }
    }
    else if ((alpha_y > threshold * 1.5 || alpha_y < -threshold * 1.5) && allow_swipe_back) {
        viewer_video_pause = false;
        // hide_overlay();
        history.back();
    }
}

function reset_media_transform() {
    transform_reset_debounce = true;
    media_scale = 1;
    media_x = 0;
    media_y = 0;
    viewer_img.style.transition = "transform 0.3s ease";
    viewer_img.style.transform = `scale(${media_scale}) translate(${media_x}px, ${media_y}px)`;
    document.getElementById('reset_transform_btn').style.display = "none";
    setTimeout(() => {
        viewer_img.style.transition = "";
        transform_reset_debounce = false;
    }, 300);
}

function sync_viewer_videotime() {
    viewer_videoseek.value = viewer_video.currentTime;
    time_current.innerHTML = new Date(viewer_video.currentTime * 1000).toISOString().substr(14, 5);
}


function next_page() {
    if (max_page <= 1) return;
    if (section != 'userlist' && inf_scroll) {
        return;
    }
    if (!current_url || current_url.includes("comments/")) {
        return;
    }
    new_page = (current_page + 1) % (max_page + 1);
    if (current_q) {
        show_loading_icon();
    }
    url = "";
    if (current_url.includes("?")) {
        url = current_url + "&p=" + new_page;
    } else {
        url = current_url + "?p=" + new_page;
    }
    window.location.href = url;
}

function prev_page() {
    if (max_page <= 1) return;
    if (section != 'userlist' && inf_scroll) {
        return;
    }
    if (!current_url || current_url.includes("comments/")) {
        return;
    }
    if (current_q) {
        show_loading_icon();
    }
    if (current_page == 1) {
        new_page = max_page;
    }
    else {
        new_page = current_page - 1;
    }
    url = "";
    if (current_url.includes("?")) {
        url = current_url + "&p=" + new_page;
    } else {
        url = current_url + "?p=" + new_page;
    }
    window.location.href = url;
}

function go_page(num = -1) {
    if (max_page <= 1) { return; }
    if (num != -1) {
        page_num = num;
    }
    else {
        page_num = prompt("Page number", "");
    }

    if (isNaN(page_num)) {
        alert("Please enter a valid number");
    }

    if (!page_num) {
        return;
    }

    page_num = Math.min(Math.max(page_num, 0), max_page)
    url = "";
    if (current_url.includes("?")) {
        url = current_url + "&p=" + page_num;
    } else {
        url = current_url + "?p=" + page_num;
    }
    window.location.href = url;
}

function add_fav(post_id) {
    fetch(url_base + "/add_fav?post_id=" + post_id, {
        method: 'GET'
    }).then(response => response.json())
        .then(data => {
            console.log(data);
            fav_divs = document.getElementsByClassName("p" + post_id + "_fav")
            for (var i = 0; i < fav_divs.length; i++) {
                fav_div = fav_divs[i];
                if (data['result'] == 'added') {
                    fav_div.src = url_base + "/img/bookmark.svg";
                }
                else {
                    fav_div.src = url_base + "/img/bookmark_empty.svg";
                }
            }
        })
}
function scrollToTop() {
    event.preventDefault();
    window.scrollTo({
        top: 0,
        left: 0,
        behavior: 'smooth'
    });
}

var hide_card_timeout = null;
function show_card_wth_timeout() {
    clearTimeout(hide_card_timeout);
    overlay_ui.style.opacity = 1;
    overlay_ui.style.pointerEvents = "auto";
    hide_card_timeout = setTimeout(() => {
        overlay_ui.style.opacity = 0;
        overlay_ui.style.pointerEvents = "none";
    }, 3000);
}

var enter_fullscreen_debounce = false;

function show_in_overlay(element) {
    event.preventDefault();
    enter_fullscreen_debounce = true;
    // overlay = document.getElementById("overlay");
    if (!overlay) {
        console.log("No overlay element found");
        return;
    }

    overlay.contentWindow.location.replace(element.getAttribute("href"));
    console.log("show in overlay", overlay.src);

    overlay.style.display = "block";
    document.body.style.overflowY = 'hidden';
    location.hash = "overlay";

    hide_navigation();
    setTimeout(() => {
        enter_fullscreen_debounce = false;
    }, 500);
    event.preventDefault();
}

function locationHashChanged() {
    console.log("hash changed", location.hash);
    if (!["#alt_text", "#overlay"].includes(location.hash)) {
        // find video in overlay and pause it
        video = overlay.querySelector("video");
        if (video) {
            video.pause();
            video.setAttribute("src", "");
        }
        // overlay.src = '';
        overlay.contentWindow.location.replace("about:blank");
        overlay.style.display = 'none';
        document.body.style.overflowY = 'auto';
        show_navigation();
        if (document.fullscreenElement) {
            document.exitFullscreen();
        }
    }
    if (location.hash != "#alt_text") {
        alt_text.style.display = "none";
    }
}

function show_navigation() {
    if (parent == window) {
        return;
    }
    if (parent && parent.show_navigation) {
        parent.show_navigation();
    }
}

function hide_navigation() {
    if (parent == window) {
        return;
    }
    if (parent && parent.hide_navigation) {
        parent.hide_navigation();
    }
}

function search(q_input = null) {
    if (q_input) {
        q = q_input;
    }
    else {
        q = search_bar_input.value;
    }
    //rip exsiting q
    if (current_url.includes("?")) {
        current_url = current_url.split("?")[0];
    }
    if (q) {
        show_loading_icon();
        add_search_history_item(q);
        window.location.href = current_url + "?q=" + q;
    }
    else {
        window.location.href = current_url;
    }
}

function external_search(q = "", type = "x") {
    console.log("external search", "q=", q, "type=", type);
    q = search_bar_input.value;
    if (q.includes("mode:full")) {
        q = q.replace("mode:full", "").trim();
    }
    q = encodeURIComponent(q);

    if (type == "x") {
        window.open("https://x.com/search?q=" + q, '_blank');
    }
    else if (type == "reddit") {
        window.open("https://www.reddit.com/search/?q=" + q, '_blank');
    }
    else if (type == "fa") {
        window.open("https://www.furaffinity.net/search/?q=" + q, '_blank');
    }
    else if (type == "bsky") {
        window.open("https://bsky.app/search?q=" + q, '_blank');
    }
}

function searchbar_on_enter(e, func) {
    if (e.key === "Enter") {
        func();
        e.preventDefault();
    }
    else if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
        e.stopPropagation();
    }
    else if (e.key === "Escape") {
        hide_search_history();
    }
}

function add_mode_full() {
    if (!search_bar_input.value) {
        search_bar_input.value = "mode:full";
        return;
    }
    if (search_bar_input.value.includes("mode:full")) {
        return;
    }
    search_bar_input.value = search_bar_input.value + " mode:full";
    search();
}

function update_search_q_placeholders() {
    q = encodeURIComponent(search_bar_input.value) || current_q;
    // find all search_q_placeholder elements and set their innerHTML to q
    search_q_placeholders = document.getElementsByClassName("search_q_placeholder");
    for (var i = 0; i < search_q_placeholders.length; i++) {
        search_q_placeholders[i].innerHTML = decodeURIComponent(q);
    }
}


function isOverflown(element) {
    return element.scrollHeight > element.clientHeight + 10;
}

function set_overflow_hidden(element) {
    const card_body_texts = element.querySelectorAll('.card_body_text');
    card_body_texts.forEach(card_body_text => {
        if (isOverflown(card_body_text)) {
            card_body_text.classList.add("fade-text");
            view_all_btn = document.createElement('a')
            view_all_btn.classList.add('view_all_btn')
            view_all_btn.setAttribute("href", card_body_text.getAttribute('post-url'))
            view_all_btn.innerText = 'View Post'
            card_body_text.after(view_all_btn)
        }
    })
}


function show_full_user_info() {
    if (!user_body_text) {
        return false;
    }
    user_body_text.style.maxHeight = "unset";
    user_body_text.style.overflow = "visible";
    user_body_text.classList.remove("fade-text");
    view_all_btn.style.display = "none";
    return false;
}


function show_content_in_frame(e, element, window = 2) {
    const url = element.getAttribute('href');
    if (parent.show_content) {
        e.preventDefault();
        parent.show_content(url, window);
    }
}

function handle_gesture() {
    if (Math.abs(touch_end_y - touch_start_y) > threshold) {
        return;
    }
    if (touch_end_x < touch_start_x - threshold) {
        next_page();
    }
    if (touch_end_x > touch_start_x + threshold) {
        prev_page();
    }
}

function get_sibling_height(id_) {
    currentElement = document.getElementById(id_);
    previousElement = currentElement.previousElementSibling;
    if (previousElement.classList.contains('card_highlighted'))
        return 0;
    height = 0;
    // check if the id matches
    if (
        previousElement &&
        previousElement.getAttribute('data-post-id') == currentElement.getAttribute('data-reply-to-id')
    ) {
        height = previousElement.scrollHeight;
    }
    return height
}

function set_reply_deco(ele) {
    id_ = ele.getAttribute('id')
    if (!id_)
        return;
    deco = ele.querySelector('.reply_deco')
    if (!deco) {
        deco = document.createElement("div");
        deco.classList.add('reply_deco');
        ele.append(deco);
    }
    sibling_height = get_sibling_height(id_);
    if (!sibling_height) {
        // console.log("no sibling height for", id_);
        return;
    }
    deco.style.height = 'calc(' + sibling_height + 'px - 3.4rem)';
    deco.style.opacity = 1;
}

function set_all_reply_decos() {
    // console.log('set_all_reply_decos()')
    document.querySelectorAll('.post_reply').forEach((e, n) => {
        console.log('set_reply_deco for', e.getAttribute('id'))
        set_reply_deco(e)
    })
    document.querySelectorAll('.reply_deco').forEach((e, n) => {
        e.style.opacity = 1;
    })
}

function update_logs(lines = 20, to_bottom = false) {
    const logs_div = document.getElementById('logs')
    fetch(url_base + '/api/logs?lines=' + lines, {
        method: 'GET'
    }).then(response => response.json())
        .then(data => {
            if (data.status == 'ok') {
                logs_div.innerHTML = ''
                data.logs.forEach(function (item, index) {
                    logs_div.innerHTML += '<div class=\'log-entry ' + item[0] + '\'>' + item[1] + '</div>';
                });
                if (to_bottom)
                    logs_div.scrollTo(0, logs_div.scrollHeight);
            }
        })
}

function append_page(depth = 0) {
    if (depth > 10) {
        console.warn("Max retry depth reached for page loading.");
        return;
    }
    if (halt_next_page_load) {
        return;
    }
    halt_next_page_load = true; // Prevent multiple loads while the current one is still in progress
    if (current_pager_page >= max_page) {
        return;
    }
    console.log("Loading page " + (current_pager_page + 1));
    current_pager_page += 1;
    fetch(`${current_url}&p=${current_pager_page}&frag_only=1`)
        .then(response => response.json())
        .then(data => {
            if (data.status === "success") {
                // create a sub-container for the new posts
                const sub_container = document.createElement('div');
                sub_container.classList.add('page_group');
                sub_container.innerHTML = data.content;
                sub_container.querySelectorAll('.post_group').forEach(el => {
                    offscreen_observer.observe(el);
                    height_observer.observe(el);
                });
                set_overflow_hidden(sub_container);
                container.appendChild(sub_container);
                page_text.textContent = `${current_pager_page} / ${max_page}`;
                halt_next_page_load = false;
            }
            else if (data.status === "continue") {
                console.log("No content in this page, trying next page...");
                halt_next_page_load = false;
                append_page(depth + 1);
            }
        })
        .catch(error => console.error('Error fetching page:', error));
}

const offscreen_observer = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
        // console.log('IntersectionObserver entry:', entry.target.id, 'isIntersecting:', entry.isIntersecting);
        if (entry.isIntersecting) {
            entry.target.classList.remove('ele_offscreen');
            entry.target.querySelectorAll('.post_reply').forEach(reply => {
                set_reply_deco(reply);
            });
        } else {
            entry.target.classList.add('ele_offscreen');
        }
    });
});

const height_observer = new ResizeObserver(entries => {
    entries.forEach(entry => {
        if (entry.contentRect.height == 0) {
            return;
        }
        // console.log('ResizeObserver entry:', entry.target.id, 'height:', entry.contentRect.height);
        entry.target.querySelectorAll('.post_reply').forEach(reply => {
            set_reply_deco(reply);
        });
    });
});

function next_media() {
    if (current_media_idx < media_cnt - 1) {
        replace_media(type, user_name, post_id, current_media_idx + 1);
        current_media_idx += 1;
    }
}

function prev_media() {
    if (current_media_idx > 0) {
        replace_media(type, user_name, post_id, current_media_idx - 1);
        current_media_idx -= 1;
    }
}

function replace_media(type, user_name, post_id, idx, skip_img_load = false) {
    viewer_video.pause();
    viewer_video.setAttribute("src", "");
    const url = `${url_base}/api/media/${type}/${user_name}/${post_id}?idx=${idx}`;
    fetch(url, { method: 'GET' })
        .then(response => response.json())
        .then(data => {
            console.log("replace_media response:", data);
            if (data.status === "success") {
                if (data.type == "video") {
                    viewer_video.setAttribute("src", data.url);
                    video_ui.style.display = "block";
                    viewer_container_video.style.display = "block";
                    viewer_container_img.style.display = "none";
                }
                else if (data.type == "image") {
                    if (!skip_img_load) {
                        viewer_img.style.opacity = 0;
                        viewer_img.setAttribute("src", data.url);
                    }
                    video_ui.style.display = "none";
                    viewer_container_video.style.display = "none";
                    viewer_container_img.style.display = "block";
                }
                if (idx == 0) {
                    media_prev_btn.style.display = "none";
                }
                else {
                    media_prev_btn.style.display = "block";
                }
                if (idx == media_cnt - 1) {
                    media_next_btn.style.display = "none";
                }
                else {
                    media_next_btn.style.display = "block";
                }
                if (data.alt) {
                    viewer_alt_btn.style.display = "inline-block";
                    viewer_alt_btn.setAttribute("data", data.alt);
                }
                else {
                    viewer_alt_btn.style.display = "none";
                }
                file_size.innerText = data.file_size;
                if (media_cnt > 3) {
                    viewer_progress.style.display = "inline-block";
                    viewer_progress.innerText = `${idx + 1} / ${media_cnt}`;
                }
            }
        })
        .catch(error => console.error('Error replacing media:', error));
}

function call_server_function(func_name, args) {
    const url = `${url_base}/api/call?func=${func_name}`;
    if (args) {
        url += `&args=${args.join(',')}`;
    }
    fetch(url, { method: 'GET' })
        .then(response => response.json())
        .then(data => {
            console.log("Server function response:", data);
            alert(data.message);
        })
        .catch(error => console.error('Error calling server function:', error));
}

function debounce(func, wait) {
    let timeout;
    return function (...args) {
        const later = () => {
            clearTimeout(timeout);
            func.apply(this, args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}

function update_search_suggestions() {
    q = search_bar_input.value;
    q = q.replace("mode:full", "").trim();
    q = q.toLowerCase();
    if (!q) {
        search_suggestions.style.display = "none";
        return;
    }
    fetch(`${url_base}/api/search_suggestions?q=${encodeURIComponent(q)}`, { method: 'GET' })
        .then(response => response.json())
        .then(data => {
            console.log("Search suggestions response:", data);
            if (data.status === "success") {
                search_suggestions.innerHTML = "";
                if (data.suggestions.length == 0) {
                    search_suggestions.style.display = "none";
                    return;
                }
                data.suggestions.forEach(suggestion => {
                    div = document.createElement("div");
                    div.classList.add("search_suggestion_item");
                    div.innerText = suggestion;
                    div.onclick = () => {
                        search_bar_input.value = suggestion;
                        search();
                    };
                    search_suggestions.appendChild(div);
                });
                search_suggestions.style.display = "block";
            }
        })
        .catch(error => console.error('Error fetching search suggestions:', error));
}

update_search_suggestions_debounced = debounce(update_search_suggestions, 300);