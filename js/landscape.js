
// get user viewport aspect ratio
function get_viewport_aspect_ratio() {
    return window.innerWidth / window.innerHeight;
}

// toggle landscape style if viewport aspect ratio is greater than 1
function toggle_landscape_style() {
    if (get_viewport_aspect_ratio() > 1) {
        landscape_style.disabled = false;
    }
    else {
        landscape_style.disabled = true;
    }
}

toggle_landscape_style();

window.addEventListener('resize', toggle_landscape_style);