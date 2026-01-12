const search_bar_input = document.getElementById("search_bar_input");
const sort_type = document.getElementById("sort_type");

var float_card_on = false;


function search(q_input = null) {
    if (q_input) {
        q = encodeURIComponent(q_input);
    }
    else {
        q = encodeURIComponent(search_bar_input.value);
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
    if (!q) {
        q = encodeURIComponent(search_bar_input.value);
    }
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
    new_page = (current_page + 1) % (max_page + 1);
    if (current_url.includes("?")) {
        window.location.href = current_url + "&p=" + new_page;
    } else {
        window.location.href = current_url + "?p=" + new_page;
    }

}

function prev_page() {
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
    window.scrollTo({
        top: 0,
        left: 0,
        behavior: 'smooth'
    });
}

const float_card = document.getElementById('float_card');

var hide_card_timeout = null;
function show_card_wth_timeout() {
    clearTimeout(hide_card_timeout);
    float_card_card.style.opacity = 1;
    hide_card_timeout = setTimeout(() => {
        float_card_card.style.opacity = 0;
    }, 3000);
}

var enter_fullscreen_debounce = false;

function show_float_card(e, card_content_url) {
    enter_fullscreen_debounce = true;
    fetch(card_content_url)
        .then(response => response.text())
        .then(data => {
            float_card.innerHTML = data;
            float_card.style.display = 'block';
            location.hash = "float_card";
            init_panzoom();
            init_video_controls();
            float_card_card = document.querySelector('.float_card .card');
            float_card_card.addEventListener('mouseover', () => {
                clearTimeout(hide_card_timeout);
                float_card_card.style.opacity = 1;
            });
            float_card_card.addEventListener('mouseleave', () => {
                clearTimeout(hide_card_timeout);
                hide_card_timeout = setTimeout(() => {
                    float_card_card.style.opacity = 0;
                }, 5000);
            });
            show_card_wth_timeout();
            float_card_on = true;
            // requests full screen
            // document.body.webkitRequestFullscreen();
            setTimeout(() => {
                enter_fullscreen_debounce = false;
            }, 400);
        });
    e.preventDefault();
}

// setInterval(() => {
//     //check if is currently in full screen
//     if (enter_fullscreen_debounce) {
//         return;
//     }
//     if (!document.webkitIsFullScreen) {
//         if (float_card_on) {
//             float_card_on = false;
//             history.back();
//         }
//     }
// }, 100);

function locationHashChanged() {
    if (!location.hash) {
        float_card.innerHTML = '';
        float_card.style.display = 'none';
    }
}

window.onhashchange = locationHashChanged;

function hide_float_card() {
    float_card_on = false;
    // document.exitFullscreen();
    history.back();
}

function init_panzoom() {
    float_card_media = document.getElementById('float_card_media');
    video_seek = document.getElementById('video_seek'); //input
    if (video_seek) {
        return;
    }
    zoom = Panzoom(float_card_media, {
        animate: false, maxScale: 8, minScale: 0.05,
        setTransform: (elem, { scale, x, y }) => {
            zoom.setStyle('transform', `scale(${scale}) translate(${x}px, ${y}px) rotate(0deg)`);
            zoom.setStyle('transition', 'none');
        }
    });
    float_card_media.addEventListener('wheel', function (e) {
        deltaY = e.deltaY;
        if (deltaY > 0) {
            zoom.zoomOut();
        }
        else {
            zoom.zoomIn();
        }
    });
    float_card_media.addEventListener('click', function (e) {
        show_card_wth_timeout();
    });
}

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
        video_seek.value = video_.currentTime;
        time_current.innerHTML = new Date(video_.currentTime * 1000).toISOString().substr(14, 5);
    });
    video_seek.addEventListener('input', function () {
        video_.currentTime = video_seek.value;
    });
    // handle click to pause/play
    video_.addEventListener('click', function () {
        // if (video_.paused) {
        //     video_.play();
        // } else {
        //     video_.pause();
        // }
        show_card_wth_timeout();
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
}
// capture left and right arrow keys to go to next/prev page
document.addEventListener('keydown', function (e) {
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

function toggle_video_play() {
    video_ = document.getElementById('float_card_media'); //video
    pause_btn_img = document.getElementById('pause_btn_img'); //img 
    if (video_.paused) {
        video_.play();
        pause_btn_img.src = url_base + "/img/pause_w.svg";
        pause_btn_img.style.marginLeft = "0.8rem";
    } else {
        video_.pause();
        pause_btn_img.src = url_base + "/img/play_w.svg";
        pause_btn_img.style.marginLeft = "0.9rem";
    }
}

function enter_fullscreen() {
    if (parent.document.fullscreenElement) {
        parent.document.exitFullscreen();
    }
    else {
        parent.document.documentElement.requestFullscreen();
    }
}

function show_search_history() {
    const search_float = document.getElementById("search_float");
    const search_history_list = document.getElementById("search_history_list");
    search_float.style.display = "block";
    search_float.style.opacity = 1;
    //load search history from localstorage
    let history_list = JSON.parse(localStorage.getItem("search_history")) || ["@fa", "@reddit", "@x", "@bsky"];
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
    let history_list = JSON.parse(localStorage.getItem("search_history")) || ["@fa", "@reddit", "@x", "@bsky"];
    //add to history if not exists
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
    add_search_history_item(decodeURIComponent(current_q));
    if (current_url.includes("?"))
        current_url = current_url + "&q=" + current_q;
    else {
        current_url = current_url + "?q=" + current_q;
    }
    if (search_bar_input)
        search_bar_input.value = decodeURIComponent(current_q);
    if (sort_type) {
        sort_type.style.display = "none";
    }
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

// find all .iconusername and add show_loading_icon on click
const icon_usernames = document.querySelectorAll('.iconusername');
icon_usernames.forEach(icon => {
    icon.addEventListener('click', show_loading_icon);
});

hide_loading_icon();