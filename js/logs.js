let live_update = false;
const live_update_checkbox = document.getElementById('live_update');

function toggle_live() {
    live_update = !live_update
    live_update_checkbox.checked = live_update
    if (live_update) {
        update_logs(200, true)
    }
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