from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class Case(db.Model):

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))


    patient_name = db.Column(
        db.String(300)
    )

    birth_date = db.Column(
        db.String(100)
    )

    organ = db.Column(
        db.String(100)
    )

    gender = db.Column(
        db.String(50)
    )

    operation_type = db.Column(
        db.String(100)
    )

    clinical = db.Column(
        db.Text
    )

    description = db.Column(
        db.Text
    )

    conclusion = db.Column(
        db.Text
    )

    images = db.Column(
        db.Text
    )

    research_number = db.Column(db.String(100))
    medical_center = db.Column(db.String(200))
    admission_date = db.Column(db.String(100))
    result_date = db.Column(db.String(100))
    doctor = db.Column(db.String(200))
    slides = db.Column(db.String(50))
    cassettes = db.Column(db.String(50))
    stain_method = db.Column(db.String(200))
    lab_worker = db.Column(db.String(200))
    material_color = db.Column(db.String(100))
    material_size = db.Column(db.String(100))
    material_consistency = db.Column(db.String(100))

class Consultant(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    first_name = db.Column(db.String(100))
    last_name = db.Column(db.String(100))
    email = db.Column(db.String(200))

    country = db.Column(db.String(100))
    city = db.Column(db.String(100))

    degree = db.Column(db.String(200))

    organization = db.Column(db.String(200))
    position = db.Column(db.String(200))

    experience = db.Column(db.String(100))

    specializations = db.Column(db.String(500))
    languages = db.Column(db.String(500))

    response_time = db.Column(db.String(100))

    consult_price = db.Column(db.String(100))

    consultant_type = db.Column(db.String(100))

    photo = db.Column(db.String(500))

    card = db.Column(db.String(100))
    owner = db.Column(db.String(200))

    documents = db.Column(db.Text)

    verified = db.Column(
        db.Boolean,
        default=False
    )

class User(db.Model):
            id = db.Column(
                db.Integer,
                primary_key=True
            )

            username = db.Column(
                db.String(100),
                unique=True
            )

            password = db.Column(
                db.String(100)
            )

            free_attempts = db.Column(
                db.Integer,
                default=10
            )

            is_consultant = db.Column(
                db.Boolean,
                default=False
            )

            is_paid = db.Column(
                db.Boolean,
                default=False
            )