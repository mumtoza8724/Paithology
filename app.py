from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    session,
    send_file
)
from model import analyze
from database import (
    db,
    Case,
    Consultant,
    User
)
from doc_generator import create_doc
from qr_generator import create_qr
from datetime import datetime

import os
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = "paithology_secret"
UPLOAD_FOLDER = "static/uploads"

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

PROFILE_FOLDER = os.path.join(UPLOAD_FOLDER, "profiles")
DOCUMENT_FOLDER = os.path.join(UPLOAD_FOLDER, "documents")

os.makedirs(PROFILE_FOLDER, exist_ok=True)
os.makedirs(DOCUMENT_FOLDER, exist_ok=True)

app.config["PROFILE_FOLDER"] = PROFILE_FOLDER
app.config["DOCUMENT_FOLDER"] = DOCUMENT_FOLDER

app.config[
    "SQLALCHEMY_DATABASE_URI"
] = "sqlite:///paithology.db"


db.init_app(app)


with app.app_context():

    db.create_all()


@app.route("/", methods=["GET", "POST"])
def home():

    if request.method == "GET":

        return render_template("index.html")


    if "user_id" not in session:

        return redirect("/login")


    user = User.query.get(
        session["user_id"]
    )


    if not user:

        session.clear()

        return redirect("/login")


    if user.free_attempts <= 0 and not user.is_paid:

        return """

        <h1>

        Бесплатные попытки закончились

        </h1>

        """


    images = request.files.getlist("images")


    image_paths = []


    for image in images:

        if image.filename == "":

            continue


        filename = secure_filename(
            image.filename
        )


        filepath = os.path.join(

            app.config["UPLOAD_FOLDER"],

            filename

        )


        image.save(filepath)


        image_paths.append(filepath)


    patient_name = request.form.get(

        "patient_name",

        ""

    )


    organ = request.form.get(

        "organ",

        ""

    )


    gender = request.form.get(

        "gender",

        ""

    )

    birth_date = request.form.get("birth_date")


    clinical = request.form.get(

        "clinical",

        ""

    )

    research_number = request.form.get("research_number")
    medical_center = request.form.get("medical_center")
    admission_date = request.form.get("admission_date")
    result_date = request.form.get("result_date")

    operation_type = request.form.get("operation_type")

    doctor = request.form.get("doctor")

    slides = request.form.get("slides")
    cassettes = request.form.get("cassettes")

    stain_method = request.form.get("stain_method")

    lab_worker = request.form.get("lab_worker")

    material_color = request.form.get("material_color")
    material_size = request.form.get("material_size")
    material_consistency = request.form.get("material_consistency")

    (
        description,

        result,

        processed,

        confidence,

        agreement,

        total_images,

        matched_images,

        key_findings

    ) = analyze(

        image_paths,

        organ,

        clinical,

        gender

    )


    new_case = Case(

        medical_center=medical_center,
        patient_name=patient_name,
        research_number=research_number,
        admission_date=admission_date,
        gender=gender,
        birth_date=birth_date,
        operation_type=operation_type,
        organ=organ,
        clinical=clinical,
        doctor=doctor,
        slides=slides,
        cassettes=cassettes,
        stain_method=stain_method,
        lab_worker=lab_worker,
        result_date=result_date,
        material_color=material_color,
        material_size=material_size,
        material_consistency=material_consistency,
        description=description,
        conclusion=result,
        images=",".join(image_paths),

        user_id = session.get("user_id")

    )


    db.session.add(
        new_case
    )


    if not user.is_paid:

        user.free_attempts -= 1


    db.session.commit()


    return render_template(

        "result.html",

        result=result,

        description=description,

        processed=processed,

        confidence=confidence,

        agreement=agreement,

        total_images=total_images,

        matched_images=matched_images,

        images=image_paths,

        organ=organ,

        gender=gender,

        clinical=clinical,

        patient_name=patient_name,

        key_findings=key_findings

    )

@app.route("/cases")
def cases():

    organ=request.args.get(
        "organ",
        ""
    )

    conclusion = request.args.get("conclusion")
    admission_date = request.args.get("admission_date")
    research_number = request.args.get("research_number")

    patient_name = request.args.get(
        "patient_name",
        ""
    )

    user_id = session.get("user_id")

    query = Case.query.filter_by(user_id=user_id)


    if organ:

        query=query.filter(
            Case.organ.contains(
                organ
            )
        )

    if admission_date:
        query = query.filter_by(admission_date=admission_date)

    if research_number:
        query = query.filter(
            Case.research_number.contains(research_number)
        )

    if conclusion:
        query = query.filter(
            Case.conclusion.contains(conclusion)
        )

    if patient_name:
        query = query.filter(
            Case.patient_name.contains(
                patient_name
            )
        )

    all_cases = query.order_by(
        Case.id.asc()
    ).all()

    for i, case in enumerate(all_cases, start=1):
        case.display_number = i

    current_user = User.query.get(session["user_id"])

    return render_template(
        "cases.html",
        cases=all_cases,
        current_user=current_user
    )

@app.route("/case/<int:id>")
def case_detail(id):

    case=Case.query.get_or_404(
        id
    )

    current_user = User.query.get(session["user_id"])

    user_cases = Case.query.filter_by(
        user_id=case.user_id
    ).order_by(
        Case.id.asc()
    ).all()

    display_number = 1

    for i, c in enumerate(user_cases, start=1):
        if c.id == case.id:
            display_number = i
            break

    return render_template(
        "case_detail.html",
        case=case,
        display_number=display_number,
        current_user=current_user
    )

@app.route("/delete_case/<int:id>")
def delete_case(id):

    case = Case.query.get_or_404(id)

    db.session.delete(case)
    db.session.commit()

    return redirect(url_for("cases"))

from doc_generator import create_doc
from flask import send_file

@app.route("/report/<int:id>")
def report(id):

    case = Case.query.get_or_404(id)

    image_path = ""

    if case.images:
        image_path = case.images.split(",")[0]

    create_doc(
        medical_center=case.medical_center,
        patient_name=case.patient_name,
        research_number=case.research_number,
        admission_date=case.admission_date,
        gender=case.gender,
        birth_date=case.birth_date,
        operation_type=case.operation_type,
        organ=case.organ,
        clinical=case.clinical,
        doctor=case.doctor,
        slides=case.slides,
        cassettes=case.cassettes,
        stain_method=case.stain_method,
        lab_worker=case.lab_worker,
        result_date=case.result_date,
        material_color=case.material_color,
        material_size=case.material_size,
        material_consistency=case.material_consistency,
        image_path=image_path,
        description=case.description,
        conclusion=case.conclusion
    )

    return send_file(
        "static/report.docx",
        as_attachment=True
    )

@app.route("/consultant-register", methods=["GET", "POST"])
def consultant_register():

    if request.method == "POST":

        first_name = request.form.get("first_name")
        last_name = request.form.get("last_name")
        email = request.form.get("email")
        degree = request.form.get("degree")

        country = request.form.get("country")
        city = request.form.get("city")

        organization = request.form.get("organization")
        position = request.form.get("position")
        experience = request.form.get("experience")

        other_specialties = request.form.getlist("other_specialty")

        all_specialties = request.form.getlist("specialization")

        for item in other_specialties:
            if item.strip():
                all_specialties.append(item.strip())

        specializations = "; ".join(all_specialties)

        languages = ", ".join(
            request.form.getlist("languages")
        )

        card = request.form.get("card")
        owner = request.form.get("owner")

        consult_price = request.form.get("consult_price")

        response_time = request.form.get("response_time")

        profile_photo = request.files.get("photo")

        photo_path = ""

        if profile_photo and profile_photo.filename:

            filename = secure_filename(profile_photo.filename)

            photo_path = os.path.join(
                app.config["PROFILE_FOLDER"],
                filename
            )

            profile_photo.save(photo_path)

            photo_path = f"uploads/profiles/{filename}"

        verification_files = request.files.getlist(
            "verification_files"
        )

        documents = []

        for file in verification_files:

            if file and file.filename:

                filename = secure_filename(file.filename)

                path = os.path.join(
                    app.config["DOCUMENT_FOLDER"],
                    filename
                )

                file.save(path)

                documents.append(filename)

        consultant = Consultant(
            first_name=first_name,
            last_name=last_name,
            email=email,

            country=country,
            city=city,

            degree=degree,

            organization=organization,
            position=position,

            experience=experience,

            specializations=specializations,
            languages=languages,

            response_time=response_time,

            consult_price=consult_price,

            photo=photo_path,

            card=card,
            owner=owner,

            documents=";".join(documents),

            verified=False
        )

        db.session.add(consultant)
        db.session.commit()

        return redirect(url_for("consultants"))

    return render_template("consultant_register.html")

@app.route("/consultants")
def consultants():

    consultants = Consultant.query.filter_by(
        verified=True
    )

    name = request.args.get("name")
    degree = request.args.get("degree")
    country = request.args.get("country")
    experience = request.args.get("experience")
    specialization = request.args.get("specialization")
    language = request.args.get("language")
    response_time = request.args.get("response_time")

    if name:
        consultants = consultants.filter(
            (Consultant.first_name.contains(name)) |
            (Consultant.last_name.contains(name))
        )

    if degree:
        consultants = consultants.filter_by(
            degree=degree
        )

    if country:
        consultants = consultants.filter(
            Consultant.country.contains(country)
        )

    if experience:
        consultants = consultants.filter_by(
            experience=experience
        )

    if specialization:
        consultants = consultants.filter(
            Consultant.specializations.contains(
                specialization
            )
        )

    if language:
        consultants = consultants.filter(
            Consultant.languages.contains(
                language
            )
        )

    if response_time:
        consultants = consultants.filter_by(
            response_time=response_time
        )

    return render_template(
        "consultants.html",
        consultants=consultants.all()
    )

@app.route(
    "/register",
    methods=["GET", "POST"]
)
def register():

    if request.method == "POST":

        username = request.form.get(
            "username"
        )

        password = request.form.get(
            "password"
        )

        existing = User.query.filter_by(
            username=username
        ).first()

        if existing:

            return "Пользователь уже существует"

        user = User(

            username=username,

            password=password

        )

        db.session.add(user)

        db.session.commit()

        return redirect("/login")

    return render_template(
        "register.html"
    )

@app.route(
    "/login",
    methods=["GET", "POST"]
)
def login():

    if request.method == "POST":

        username = request.form.get(
            "username"
        )

        password = request.form.get(
            "password"
        )

        user = User.query.filter_by(
            username=username,
            password=password
        ).first()

        if user:

            session["user_id"] = user.id

            return redirect("/")

        return "Неверный логин или пароль"

    return render_template(
        "login.html"
    )

@app.route("/logout")
def logout():

    session.clear()

    return redirect("/")

@app.route("/buy-pro")
def buy_pro():

    if "user_id" not in session:

        return redirect("/login")

    user = User.query.get(
        session["user_id"]
    )

    user.is_paid = True

    db.session.commit()

    return """

    <h1>
    PRO подписка активирована
    </h1>

    <a href="/">
    Вернуться назад
    </a>

    """

@app.route("/admin/consultants")
def admin_consultants():

    consultants = Consultant.query.all()

    return render_template(
        "admin_consultants.html",
        consultants=consultants
    )

@app.route("/approve_consultant/<int:id>")
def approve_consultant(id):

    consultant = Consultant.query.get_or_404(id)

    consultant.verified = True

    db.session.commit()

    return redirect(url_for("admin_consultants"))

@app.route("/reject_consultant/<int:id>")
def reject_consultant(id):

    consultant = Consultant.query.get_or_404(id)

    db.session.delete(consultant)

    db.session.commit()

    return redirect(url_for("admin_consultants"))

if __name__=="__main__":

    app.run(
        debug=True
    )