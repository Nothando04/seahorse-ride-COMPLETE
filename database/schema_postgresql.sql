-- Seahorse Ride database schema (postgresql). Generated from seahorse/models.py.
-- Normally you do not run this by hand: python app.py creates it.

CREATE TABLE add_on (
	id SERIAL NOT NULL, 
	name VARCHAR(120) NOT NULL, 
	description VARCHAR(300), 
	price_cents INTEGER, 
	price_basis VARCHAR(12) NOT NULL, 
	is_active BOOLEAN NOT NULL, 
	CONSTRAINT pk_add_on PRIMARY KEY (id)
);

CREATE TABLE enquiry (
	id SERIAL NOT NULL, 
	name VARCHAR(120) NOT NULL, 
	email VARCHAR(254) NOT NULL, 
	phone VARCHAR(30), 
	subject VARCHAR(150) NOT NULL, 
	message TEXT NOT NULL, 
	status VARCHAR(10) NOT NULL, 
	reply TEXT, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	CONSTRAINT pk_enquiry PRIMARY KEY (id)
);

CREATE TABLE experience (
	id SERIAL NOT NULL, 
	slug VARCHAR(80) NOT NULL, 
	kind VARCHAR(12) NOT NULL, 
	name VARCHAR(120) NOT NULL, 
	summary VARCHAR(300) NOT NULL, 
	description TEXT NOT NULL, 
	duration_minutes INTEGER, 
	price_cents INTEGER, 
	price_basis VARCHAR(12) NOT NULL, 
	min_group INTEGER NOT NULL, 
	max_group INTEGER, 
	min_age INTEGER, 
	difficulty VARCHAR(30), 
	requirements TEXT, 
	inclusions TEXT, 
	exclusions TEXT, 
	terms TEXT, 
	safety_notes TEXT, 
	availability_note VARCHAR(200), 
	image_path VARCHAR(255), 
	is_active BOOLEAN NOT NULL, 
	is_featured BOOLEAN NOT NULL, 
	sort_order INTEGER NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	CONSTRAINT pk_experience PRIMARY KEY (id), 
	CONSTRAINT ck_experience_price_nonneg CHECK (price_cents is null or price_cents >= 0), 
	CONSTRAINT ck_experience_min_group_pos CHECK (min_group >= 1), 
	CONSTRAINT uq_experience_slug UNIQUE (slug)
);

CREATE TABLE gallery_image (
	id SERIAL NOT NULL, 
	path VARCHAR(255) NOT NULL, 
	caption VARCHAR(200), 
	category VARCHAR(30), 
	consent_confirmed BOOLEAN NOT NULL, 
	is_published BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	CONSTRAINT pk_gallery_image PRIMARY KEY (id)
);

CREATE TABLE horse (
	id SERIAL NOT NULL, 
	name VARCHAR(80) NOT NULL, 
	category VARCHAR(20) DEFAULT 'beginner' NOT NULL, 
	temperament VARCHAR(200), 
	suitable_for VARCHAR(200), 
	max_rider_weight_kg INTEGER, 
	status VARCHAR(20) NOT NULL, 
	notes TEXT, 
	photo_path VARCHAR(255), 
	show_publicly BOOLEAN NOT NULL, 
	breed VARCHAR(80), 
	colour VARCHAR(40), 
	sex VARCHAR(10), 
	birth_year INTEGER, 
	height_hands VARCHAR(10), 
	joined_year INTEGER, 
	history TEXT, 
	fun_fact VARCHAR(300), 
	favourite_treat VARCHAR(80), 
	CONSTRAINT pk_horse PRIMARY KEY (id)
);

CREATE TABLE setting (
	key VARCHAR(60) NOT NULL, 
	value TEXT NOT NULL, 
	CONSTRAINT pk_setting PRIMARY KEY (key)
);

CREATE TABLE "user" (
	id SERIAL NOT NULL, 
	email VARCHAR(254) NOT NULL, 
	name VARCHAR(120) NOT NULL, 
	phone VARCHAR(30), 
	password_hash VARCHAR(255) NOT NULL, 
	role VARCHAR(20) NOT NULL, 
	email_verified BOOLEAN NOT NULL, 
	marketing_opt_in BOOLEAN NOT NULL, 
	is_active BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	last_login_at TIMESTAMP WITHOUT TIME ZONE, 
	CONSTRAINT pk_user PRIMARY KEY (id), 
	CONSTRAINT ck_user_role_valid CHECK (role in ('customer', 'guide', 'booking_admin', 'manager', 'super_admin'))
);

CREATE TABLE waiver (
	id SERIAL NOT NULL, 
	version INTEGER NOT NULL, 
	title VARCHAR(150) NOT NULL, 
	body TEXT NOT NULL, 
	is_active BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	CONSTRAINT pk_waiver PRIMARY KEY (id), 
	CONSTRAINT uq_waiver_version UNIQUE (version)
);

CREATE TABLE audit_log (
	id SERIAL NOT NULL, 
	user_id INTEGER, 
	action VARCHAR(60) NOT NULL, 
	entity VARCHAR(40), 
	entity_id VARCHAR(40), 
	detail TEXT, 
	ip_address VARCHAR(64), 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	CONSTRAINT pk_audit_log PRIMARY KEY (id), 
	CONSTRAINT fk_audit_log_user_id_user FOREIGN KEY(user_id) REFERENCES "user" (id)
);

CREATE TABLE slot (
	id SERIAL NOT NULL, 
	date DATE NOT NULL, 
	start_time VARCHAR(5) NOT NULL, 
	end_time VARCHAR(5) NOT NULL, 
	capacity INTEGER NOT NULL, 
	status VARCHAR(12) NOT NULL, 
	block_reason VARCHAR(200), 
	experience_id INTEGER, 
	CONSTRAINT pk_slot PRIMARY KEY (id), 
	CONSTRAINT uq_slot_date_start UNIQUE (date, start_time), 
	CONSTRAINT ck_slot_capacity_nonneg CHECK (capacity >= 0), 
	CONSTRAINT fk_slot_experience_id_experience FOREIGN KEY(experience_id) REFERENCES experience (id)
);

CREATE TABLE booking (
	id SERIAL NOT NULL, 
	reference VARCHAR(16) NOT NULL, 
	access_key VARCHAR(64) NOT NULL, 
	kind VARCHAR(10) NOT NULL, 
	status VARCHAR(40) NOT NULL, 
	user_id INTEGER, 
	experience_id INTEGER, 
	slot_id INTEGER, 
	preferred_date DATE NOT NULL, 
	preferred_time VARCHAR(5), 
	alternative_date DATE, 
	adults INTEGER NOT NULL, 
	children INTEGER NOT NULL, 
	riders INTEGER NOT NULL, 
	spectators INTEGER NOT NULL, 
	rider_details TEXT, 
	addon_ids TEXT, 
	event_type VARCHAR(60), 
	location VARCHAR(200), 
	horse_preference VARCHAR(20), 
	budget VARCHAR(60), 
	duration_wish VARCHAR(60), 
	activities TEXT, 
	attachment_path VARCHAR(255), 
	contact_name VARCHAR(120) NOT NULL, 
	contact_email VARCHAR(254) NOT NULL, 
	contact_phone VARCHAR(30) NOT NULL, 
	preferred_contact VARCHAR(20), 
	accessibility TEXT, 
	special_requests TEXT, 
	terms_accepted_at TIMESTAMP WITHOUT TIME ZONE, 
	estimate_cents INTEGER, 
	final_price_cents INTEGER, 
	currency VARCHAR(3) NOT NULL, 
	payment_mode VARCHAR(10), 
	deposit_cents INTEGER, 
	payment_deadline TIMESTAMP WITHOUT TIME ZONE, 
	approval_note TEXT, 
	approved_at TIMESTAMP WITHOUT TIME ZONE, 
	approved_by_id INTEGER, 
	guide_id INTEGER, 
	reminder_sent_at TIMESTAMP WITHOUT TIME ZONE, 
	checked_in_at TIMESTAMP WITHOUT TIME ZONE, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	CONSTRAINT pk_booking PRIMARY KEY (id), 
	CONSTRAINT ck_booking_counts_nonneg CHECK (riders >= 0 and adults >= 0 and children >= 0 and spectators >= 0), 
	CONSTRAINT ck_booking_final_nonneg CHECK (final_price_cents is null or final_price_cents >= 0), 
	CONSTRAINT uq_booking_access_key UNIQUE (access_key), 
	CONSTRAINT fk_booking_user_id_user FOREIGN KEY(user_id) REFERENCES "user" (id), 
	CONSTRAINT fk_booking_experience_id_experience FOREIGN KEY(experience_id) REFERENCES experience (id), 
	CONSTRAINT fk_booking_slot_id_slot FOREIGN KEY(slot_id) REFERENCES slot (id), 
	CONSTRAINT fk_booking_approved_by_id_user FOREIGN KEY(approved_by_id) REFERENCES "user" (id), 
	CONSTRAINT fk_booking_guide_id_user FOREIGN KEY(guide_id) REFERENCES "user" (id)
);

CREATE TABLE booking_horses (
	booking_id INTEGER NOT NULL, 
	horse_id INTEGER NOT NULL, 
	CONSTRAINT pk_booking_horses PRIMARY KEY (booking_id, horse_id), 
	CONSTRAINT fk_booking_horses_booking_id_booking FOREIGN KEY(booking_id) REFERENCES booking (id) ON DELETE CASCADE, 
	CONSTRAINT fk_booking_horses_horse_id_horse FOREIGN KEY(horse_id) REFERENCES horse (id) ON DELETE CASCADE
);

CREATE TABLE message (
	id SERIAL NOT NULL, 
	booking_id INTEGER NOT NULL, 
	sender VARCHAR(10) NOT NULL, 
	user_id INTEGER, 
	body TEXT NOT NULL, 
	internal BOOLEAN NOT NULL, 
	read_by_customer BOOLEAN NOT NULL, 
	read_by_admin BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	CONSTRAINT pk_message PRIMARY KEY (id), 
	CONSTRAINT fk_message_booking_id_booking FOREIGN KEY(booking_id) REFERENCES booking (id) ON DELETE CASCADE, 
	CONSTRAINT fk_message_user_id_user FOREIGN KEY(user_id) REFERENCES "user" (id)
);

CREATE TABLE notification (
	id SERIAL NOT NULL, 
	to_email VARCHAR(254) NOT NULL, 
	subject VARCHAR(200) NOT NULL, 
	body_text TEXT NOT NULL, 
	channel VARCHAR(10) NOT NULL, 
	status VARCHAR(10) NOT NULL, 
	attempts INTEGER NOT NULL, 
	last_error VARCHAR(300), 
	booking_id INTEGER, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	sent_at TIMESTAMP WITHOUT TIME ZONE, 
	CONSTRAINT pk_notification PRIMARY KEY (id), 
	CONSTRAINT fk_notification_booking_id_booking FOREIGN KEY(booking_id) REFERENCES booking (id)
);

CREATE TABLE payment (
	id SERIAL NOT NULL, 
	booking_id INTEGER NOT NULL, 
	m_payment_id VARCHAR(40) NOT NULL, 
	provider VARCHAR(20) NOT NULL, 
	kind VARCHAR(10) NOT NULL, 
	amount_cents INTEGER NOT NULL, 
	status VARCHAR(12) NOT NULL, 
	gateway_payment_id VARCHAR(60), 
	method_note VARCHAR(200), 
	recorded_by_id INTEGER, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	paid_at TIMESTAMP WITHOUT TIME ZONE, 
	CONSTRAINT pk_payment PRIMARY KEY (id), 
	CONSTRAINT ck_payment_amount_pos CHECK (amount_cents > 0), 
	CONSTRAINT fk_payment_booking_id_booking FOREIGN KEY(booking_id) REFERENCES booking (id), 
	CONSTRAINT uq_payment_m_payment_id UNIQUE (m_payment_id), 
	CONSTRAINT uq_payment_gateway_payment_id UNIQUE (gateway_payment_id), 
	CONSTRAINT fk_payment_recorded_by_id_user FOREIGN KEY(recorded_by_id) REFERENCES "user" (id)
);

CREATE TABLE refund (
	id SERIAL NOT NULL, 
	booking_id INTEGER NOT NULL, 
	amount_cents INTEGER NOT NULL, 
	reason VARCHAR(300), 
	status VARCHAR(12) NOT NULL, 
	created_by_id INTEGER, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	completed_at TIMESTAMP WITHOUT TIME ZONE, 
	CONSTRAINT pk_refund PRIMARY KEY (id), 
	CONSTRAINT fk_refund_booking_id_booking FOREIGN KEY(booking_id) REFERENCES booking (id), 
	CONSTRAINT fk_refund_created_by_id_user FOREIGN KEY(created_by_id) REFERENCES "user" (id)
);

CREATE TABLE review (
	id SERIAL NOT NULL, 
	booking_id INTEGER NOT NULL, 
	display_name VARCHAR(60) NOT NULL, 
	rating INTEGER NOT NULL, 
	comment TEXT NOT NULL, 
	status VARCHAR(10) NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	CONSTRAINT pk_review PRIMARY KEY (id), 
	CONSTRAINT ck_review_rating_range CHECK (rating between 1 and 5), 
	CONSTRAINT uq_review_booking_id UNIQUE (booking_id), 
	CONSTRAINT fk_review_booking_id_booking FOREIGN KEY(booking_id) REFERENCES booking (id)
);

CREATE TABLE status_history (
	id SERIAL NOT NULL, 
	booking_id INTEGER NOT NULL, 
	from_status VARCHAR(40), 
	to_status VARCHAR(40) NOT NULL, 
	changed_by_id INTEGER, 
	actor_label VARCHAR(60), 
	note TEXT, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	CONSTRAINT pk_status_history PRIMARY KEY (id), 
	CONSTRAINT fk_status_history_booking_id_booking FOREIGN KEY(booking_id) REFERENCES booking (id) ON DELETE CASCADE, 
	CONSTRAINT fk_status_history_changed_by_id_user FOREIGN KEY(changed_by_id) REFERENCES "user" (id)
);

CREATE TABLE waiver_signature (
	id SERIAL NOT NULL, 
	booking_id INTEGER NOT NULL, 
	waiver_id INTEGER NOT NULL, 
	rider_name VARCHAR(120) NOT NULL, 
	rider_is_minor BOOLEAN NOT NULL, 
	signer_name VARCHAR(120) NOT NULL, 
	emergency_name VARCHAR(120) NOT NULL, 
	emergency_phone VARCHAR(30) NOT NULL, 
	medical_notes VARCHAR(500), 
	ip_address VARCHAR(64), 
	signed_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	CONSTRAINT pk_waiver_signature PRIMARY KEY (id), 
	CONSTRAINT fk_waiver_signature_booking_id_booking FOREIGN KEY(booking_id) REFERENCES booking (id) ON DELETE CASCADE, 
	CONSTRAINT fk_waiver_signature_waiver_id_waiver FOREIGN KEY(waiver_id) REFERENCES waiver (id)
);

CREATE TABLE payment_event (
	id SERIAL NOT NULL, 
	payment_id INTEGER, 
	outcome VARCHAR(30) NOT NULL, 
	detail VARCHAR(300), 
	payload TEXT, 
	remote_ip VARCHAR(64), 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	CONSTRAINT pk_payment_event PRIMARY KEY (id), 
	CONSTRAINT fk_payment_event_payment_id_payment FOREIGN KEY(payment_id) REFERENCES payment (id)
);
