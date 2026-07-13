const agencies = {
    msit: document.getElementById("msit-list"),
    mcee: document.getElementById("mcee-list"),
    motir: document.getElementById("motir-list")
};

fetch("data.json")
    .then(response => response.json())
    .then(data => {

        document.getElementById("lastUpdate").textContent = data.last_update;

        renderAgency("msit", data.msit);
        renderAgency("mcee", data.mcee);
        renderAgency("motir", data.motir);

    })
    .catch(error => {

        console.error(error);

        Object.values(agencies).forEach(list => {

            list.innerHTML =
                "<li class='empty'>데이터를 불러올 수 없습니다.</li>";

        });

    });

function renderAgency(id, articles){

    const list = agencies[id];

    list.innerHTML = "";

    if(!articles || articles.length === 0){

        list.innerHTML =
            "<li class='empty'>오늘 등록된 보도자료 없음</li>";

        return;
    }

    articles.forEach(article=>{

        const li=document.createElement("li");

        const a=document.createElement("a");

        a.href=article.link;
        a.target="_blank";
        a.textContent=article.title;

        li.appendChild(a);

        list.appendChild(li);

    });

}

document
.getElementById("refreshBtn")
.addEventListener("click",()=>{

    location.reload();

});
