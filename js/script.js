const search_bar_input = document.getElementById("search_bar_input");
const landscape_style = document.getElementById("landscape_style");
var float_card = document.getElementById("float_card");
const alt_text = document.getElementById("alt_text");
const alt_text_text = document.getElementById("alt_text_text");
const toast_div = document.getElementById("toast_div");
const container = document.getElementById("container");
const container_outer = document.getElementById("container_outer");
var float_card_on = false;

var toast_timeout = null;
function toast(message, onclick_href = "#") {
    if (!message) return;
    console.log("[TOAST] " + message)
    toast_div.innerText = message;
    toast_div.style.pointerEvents = "all";
    toast_div.style.opacity = 0.9;
    clearTimeout(toast_timeout);
    toast_timeout = setTimeout(() => {
        toast_div.style.pointerEvents = "none";
        toast_div.style.opacity = 0;
    }, 5300)
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

var page_control = document.getElementById("page_control");

if (max_page <= 1) {
    page_control.style.display = "none";
}

function next_page() {
    if (!current_url || current_url.includes("comments/")) {
        return;
    }
    new_page = (current_page + 1) % (max_page + 1);
    if (current_q) {
        show_loading_icon();
    }
    if (current_url.includes("?")) {
        window.location.href = current_url + "&p=" + new_page;
    } else {
        window.location.href = current_url + "?p=" + new_page;
    }

}

function prev_page() {
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
    if (current_url.includes("?")) {
        window.location.href = current_url + "&p=" + new_page;
    } else {
        window.location.href = current_url + "?p=" + new_page;
    }
}

function go_page(num = -1) {
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
    if (current_url.includes("?")) {
        window.location.href = current_url + "&p=" + page_num;
    } else {
        window.location.href = current_url + "?p=" + page_num;
    }
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
    float_card_ui.style.opacity = 1;
    float_card_ui.style.pointerEvents = "auto";
    hide_card_timeout = setTimeout(() => {
        float_card_ui.style.opacity = 0;
        float_card_ui.style.pointerEvents = "none";
    }, 3000);
}

var enter_fullscreen_debounce = false;

function show_float_card(e, card_content_url) {
    enter_fullscreen_debounce = true;
    fetch(card_content_url)
        .then(response => response.text())
        .then(data => {
            if (parent && parent.hide_navigation) {
                parent.hide_navigation();
            }
            float_card.innerHTML = data;
            float_card.style.display = 'block';
            // clear event listeners on float_card
            var new_float_card = float_card.cloneNode(true);
            float_card.parentNode.replaceChild(new_float_card, float_card);
            float_card = document.getElementById("float_card");
            location.hash = "float_card";
            document.body.style.overflowY = 'hidden';
            media_x = 0;
            media_y = 0;
            media_scale = 1;
            is_pointer_down = false;
            init_img_controls();
            init_video_controls();
            float_card_ui = document.getElementById("float_card_ui");
            float_card_ui.addEventListener('pointermove', () => {
                clearTimeout(hide_card_timeout);
                float_card_ui.style.opacity = 1;
                hide_card_timeout = setTimeout(() => {
                    float_card_ui.style.opacity = 0;
                    float_card_ui.style.pointerEvents = "none";
                }, 5000);
            });
            float_card_ui.addEventListener('pointerleave', () => {
                clearTimeout(hide_card_timeout);
                hide_card_timeout = setTimeout(() => {
                    float_card_ui.style.opacity = 0;
                    float_card_ui.style.pointerEvents = "none";
                }, 5000);
            });
            full_card_media = document.getElementById('float_card_media');
            if (full_card_media.tagName.toLowerCase() === "img") {
                full_card_media.addEventListener('load', () => {
                    full_card_media.style.opacity = 1;
                });
                // show media after 1s even if load event not fired, to prevent stuck when load event not fired due to browser issue
                setTimeout(() => {
                    full_card_media.style.opacity = 1;
                }, 1000);
            }
            else {
                full_card_media.style.opacity = 1;
            };

            show_card_wth_timeout();
            float_card_on = true;
            setTimeout(() => {
                enter_fullscreen_debounce = false;
            }, 400);
        });
    e.preventDefault();
}

function locationHashChanged() {
    console.log("hash changed", location.hash);
    if (!location.hash) {
        // find video in float_card and pause it
        video = float_card.querySelector("video");
        if (video) {
            video.pause();
            video.setAttribute("src", "");
        }
        float_card.innerHTML = '';
        float_card.style.display = 'none';
        document.body.style.overflowY = 'auto';
        if (parent && parent.show_navigation) {
            parent.show_navigation();
        }
        if (document.fullscreenElement) {
            document.exitFullscreen();
        }
    }
    if (location.hash != "#alt_text") {
        alt_text.style.display = "none";
    }
}

window.onhashchange = locationHashChanged;

function hide_float_card() {
    float_card_on = false;
    if (document.fullscreenElement) {
        document.exitFullscreen();
    }
    if (parent && parent.show_navigation) {
        parent.show_navigation();
    }
    history.back();
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
    float_card_media = document.getElementById('float_card_media');
    video_seek = document.getElementById('video_seek'); //input
    reset_transform_btn = document.getElementById('reset_transform_btn');
    if (video_seek) {
        return;
    }
    float_card.addEventListener('wheel', function (e) {
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
        float_card_media.style.transform = `scale(${media_scale}) translate(${media_x}px, ${media_y}px)`;
    });
    float_card.addEventListener('pointerdown', function (e) {
        if (transform_reset_debounce) {
            return;
        }
        is_pointer_down = true;
        down_x = e.clientX;
        down_y = e.clientY;
        // console.log("pointer down");
    });
    float_card.addEventListener('pointermove', function (e) {
        if (media_scale == 1) {
            return;
        }
        if (is_pointer_down) {
            media_x += e.movementX / media_scale;
            media_y += e.movementY / media_scale;
            float_card_media.style.transform = `scale(${media_scale}) translate(${media_x}px, ${media_y}px)`;
        }
    });
    float_card.addEventListener('touchmove', function (e) {
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
                float_card_media.style.transform = `scale(${media_scale}) translate(${media_x}px, ${media_y}px)`;
            }
            touch_distance = current_distance;
        }
    });
    float_card.addEventListener('pointerup', function (e) {
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
    float_card.addEventListener('pointerleave', function (e) {
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
            full_card_prev = document.getElementById("full_card_prev");
            video_pause = false;
            if (full_card_prev) {
                full_card_prev.click();
            }
        }
        else if (alpha_x < -threshold && allow_next_prev) {
            full_card_next = document.getElementById("full_card_next");
            video_pause = false;
            if (full_card_next) {
                full_card_next.click();
            }
        }
    }
    else if ((alpha_y > threshold * 1.5 || alpha_y < -threshold * 1.5) && allow_swipe_back) {
        video_pause = false;
        hide_float_card();
    }
}

function reset_media_transform() {
    transform_reset_debounce = true;
    media_scale = 1;
    media_x = 0;
    media_y = 0;
    float_card_media.style.transition = "transform 0.3s ease";
    float_card_media.style.transform = `scale(${media_scale}) translate(${media_x}px, ${media_y}px)`;
    document.getElementById('reset_transform_btn').style.display = "none";
    setTimeout(() => {
        float_card_media.style.transition = "";
        transform_reset_debounce = false;
    }, 300);
}

function sync_video_time() {
    video_seek.value = video_.currentTime;
    time_current.innerHTML = new Date(video_.currentTime * 1000).toISOString().substr(14, 5);
}

var video_pause = false;

function init_video_controls() {
    console.log("init video controls");
    video_ = document.getElementById('float_card_media'); //video
    video_seek = document.getElementById('video_seek'); //input
    time_current = document.getElementById('time_current'); //current time MM:SS
    time_total = document.getElementById('time_total'); //total time, MM:SS
    if (!video_seek) {
        console.log("no video seek");
        return;
    }
    video_.addEventListener('loadedmetadata', function () {
        video_seek.max = video_.duration;
        time_total.innerHTML = new Date(video_.duration * 1000).toISOString().substr(14, 5);
    });
    video_.addEventListener('timeupdate', function () {
        sync_video_time();
    });
    video_seek.addEventListener('input', function () {
        video_.currentTime = video_seek.value;
    });
    //wheel to seek
    video_.addEventListener('wheel', function (e) {
        deltaY = e.deltaY;
        if (deltaY > 0) {
            video_.currentTime += 2;
        }
        else {
            video_.currentTime -= 2;
        }
    });

    let time_when_pointer_down = 0;
    float_card.addEventListener('pointerdown', function (e) {
        is_pointer_down = true;
        down_x = e.clientX;
        down_y = e.clientY;
        time_when_pointer_down = video_.currentTime;
    });
    float_card.addEventListener('pointermove', function (e) {
        if (is_pointer_down) {
            video_.pause()
            move_x = e.clientX - down_x;
            move_y = e.clientY - down_y;
            if (Math.abs(move_y) > 10 && Math.abs(move_y) > Math.abs(move_x)) {
                return;
            }
            if (Math.abs(move_x) > 10) {
                video_.currentTime = time_when_pointer_down + move_x / 50;
            }
            sync_video_time();
        }
    });
    float_card.addEventListener('touchmove', function (e) {
        if (is_pointer_down) {
            video_.pause()
            move_x = e.touches[0].clientX - down_x;
            move_y = e.touches[0].clientY - down_y;
            if (Math.abs(move_y) > 10 && Math.abs(move_y) > Math.abs(move_x)) {
                return;
            }
            if (Math.abs(move_x) > 10) {
                video_.currentTime = time_when_pointer_down + move_x / 50;
            }
            sync_video_time();
        }
    });
    float_card.addEventListener('pointerup', function (e) {
        is_pointer_down = false;
        swipe_to_next_prev(e, allow_swipe_back = true, allow_next_prev = false);
        up_x = e.clientX;
        up_y = e.clientY;
        if (Math.abs(up_x - down_x) < 5 && Math.abs(up_y - down_y) < 5) {
            show_card_wth_timeout();
        }
        if (!video_pause)
            video_.play();
        video_pause = false;
    });
    float_card.addEventListener('pointerleave', function (e) {
        is_pointer_down = false;
        if (!video_pause && float_card_on)
            video_.play();
        video_pause = false;
    });
}


function toggle_video_play() {
    video_ = document.getElementById('float_card_media'); //video
    pause_btn_img = document.getElementById('pause_btn_img'); //img 
    if (video_.paused) {
        video_.play();
        video_pause = false;
        pause_btn_img.src = url_base + "/img/pause_w.svg";
        pause_btn_img.style.marginLeft = "0.8rem";
    } else {
        video_.pause();
        video_pause = true;
        pause_btn_img.src = url_base + "/img/play_w.svg";
        pause_btn_img.style.marginLeft = "0.9rem";
    }
}

// capture left and right arrow keys to go to next/prev page
document.addEventListener('keydown', function (e) {
    if (float_card && float_card.style.display === 'block') {
        if (e.key === 'Escape') {
            hide_float_card();
        }
        else if (e.key === 'ArrowRight') {
            full_next_btn = document.getElementById("full_card_next");
            if (full_next_btn) {
                full_next_btn.click();
            }
        } else if (e.key === 'ArrowLeft') {
            full_prev_btn = document.getElementById("full_card_prev");
            if (full_prev_btn) {
                full_prev_btn.click();
            }
        }
        return;
    }
    if (e.key === 'ArrowLeft') {
        prev_page();
    } else if (e.key === 'ArrowRight') {
        next_page();
    }
    else if (e.key === 'Escape') {
        if (float_card_on) {
            float_card_on = false;
            history.back();
        }
    }
})

function enter_fullscreen() {
    if (parent.document.fullscreenElement) {
        parent.document.exitFullscreen();
    }
    else {
        parent.document.documentElement.requestFullscreen();
    }
}

function viewer_enter_fullscreen() {
    if (document.fullscreenElement) {
        document.exitFullscreen();
    }
    else {
        float_card.requestFullscreen();
    }

}

function show_search_history() {
    const search_float = document.getElementById("search_float");
    const search_history_list = document.getElementById("search_history_list");
    search_float.style.display = "block";
    search_float.style.opacity = 1;
    //load search history from localstorage
    history_list = JSON.parse(localStorage.getItem("search_history")) || ["@fa", "@reddit", "@x", "@bsky", "@patreon", "mode:full"];
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
    history_list = JSON.parse(localStorage.getItem("search_history")) || ["@fa", "@reddit", "@x", "@bsky", "@patreon", "mode:full"];
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

if (current_q) {
    add_search_history_item(current_q);
    if (current_url.includes("?"))
        current_url = current_url + "&q=" + current_q;
    else {
        current_url = current_url + "?q=" + current_q;
    }
    if (search_bar_input)
        search_bar_input.value = decodeURIComponent(current_q);
    update_search_q_placeholders();
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

function select_user(uid) {
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
    group_btn.innerText = "Group Selected (" + select_users.length + ")";
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
    const group_name = prompt("Group name", selected_users[0].id.substring(1) + " and " + (selected_users.length - 1) + " more");
    if (!group_name) {
        return;
    }
    const uids = Array.from(selected_users).map(toggle => toggle.id.substring(1));
    fetch(url_base + "/api/group_users", {
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

function ungroup_users(group_name) {
    if (!group_name) {
        return;
    }
    fetch(url_base + "/api/ungroup_users", {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({
            group_name: group_name
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
    event.stopPropagation();
}

function hide_alt() {
    alt_text.style.display = "none";
    history.back();
}

function add_rescan(url) {
    add_job(url, 0, 0, 1)
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

function add_job(url, full, media_only, rescan = false) {
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
                if (data.current)
                    document.getElementById('current').innerHTML = data.current;
                else
                    document.getElementById('current').innerHTML = 'Idle';
            }
            if (queue_div) {
                queue_div.innerHTML = '';
                if (data.queue) {
                    data.queue.forEach(function (item, index) {
                        queue_div.innerHTML += '<div class=\'job\'>' + item[0] + '</div>';
                    });
                }
            }
        });
    hide_user_context_menu();
}

function url_input() {
    url = document.getElementById('url_input').value;
    full = document.getElementById('full').checked;
    media_only = document.getElementById('media_only').checked;
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


// find all .iconusername and add show_loading_icon on click
const icon_usernames = document.querySelectorAll('.iconusername');
icon_usernames.forEach(icon => {
    icon.addEventListener('click', show_loading_icon);
});

function isOverflown(element) {
    return element.scrollHeight > element.clientHeight + 10;
}

const card_body_texts = document.querySelectorAll('.card_body_text');
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

const user_body_text = document.querySelector('.user_body_text');

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

if (user_body_text) {
    if (isOverflown(user_body_text)) {
        user_body_text.classList.add("fade-text");
        view_all_btn = document.createElement('a')
        view_all_btn.classList.add('view_all_btn')
        view_all_btn.setAttribute("onclick", "show_full_user_info()")
        view_all_btn.innerText = 'Expand'
        user_body_text.after(view_all_btn)
    }
}


function show_content_in_frame(e, element, window = 2) {
    const url = element.getAttribute('href');
    if (parent.show_content) {
        e.preventDefault();
        parent.show_content(url, window);
    }
}

hide_loading_icon();

if (parent && parent.show_navigation) {
    parent.show_navigation();
}

// swipe left and right to go to next/prev page on mobile
var touch_start_x = 0;
var touch_end_x = 0;
var touch_start_y = 0;
var touch_end_y = 0;

container_outer.addEventListener('touchstart', function (e) {
    touch_start_x = e.changedTouches[0].screenX;
    touch_start_y = e.changedTouches[0].screenY;
}, false);

container_outer.addEventListener('touchend', function (e) {
    touch_end_x = e.changedTouches[0].screenX;
    touch_end_y = e.changedTouches[0].screenY;
    handle_gesture();
}, false);

threshold = 60;

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
        height = previousElement.offsetHeight;
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
    if (!sibling_height)
        return;
    deco.style.height = 'calc(' + sibling_height + 'px - 3.9rem)';
}

function set_all_reply_decos() {
    console.log('set_all_reply_decos()')
    document.querySelectorAll('.post_reply').forEach((e, n) => {
        set_reply_deco(e)
    })
    document.querySelectorAll('.reply_deco').forEach((e, n) => {
        e.style.opacity = 1;
    })
}

window.onload = () => {
    set_all_reply_decos();
}

window.addEventListener('resize', set_all_reply_decos);